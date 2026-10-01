"""任务执行引擎：子进程执行、实时输出、超时终止、失败重试。"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from . import store
from .config import DEFAULT_ENV, BASE_DIR
from .notify import notify_task_result

IS_WINDOWS = sys.platform.startswith("win")
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0

MAX_OUTPUT_BYTES = 512 * 1024  # 单条运行记录最多保留 512KB 输出
LIVE_TTL = 120                 # 运行结束后实时输出缓冲保留的秒数

AFTER_POLL_INTERVAL = 5        # 收尾命令等待循环的轮询间隔（秒）
AFTER_UPCOMING_WINDOW = 300    # 有任务将在该窗口内定时启动时，收尾命令继续等待（秒）
AFTER_FREQUENT_PERIOD = 600    # 运行周期短于该秒数的高频任务（如每5分钟巡检）不阻塞收尾命令
AFTER_MAX_WAIT = 2 * 3600      # 收尾命令最长等待时间（秒），超时强制触发


def decode_bytes(data: bytes) -> str:
    for enc in ("utf-8", "gbk", "mbcs" if IS_WINDOWS else "latin-1", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError, OSError):
            continue
    return data.decode("utf-8", errors="replace")


def _kill_tree(proc: subprocess.Popen) -> None:
    """终止进程及其子进程树。"""
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=10)
        else:
            import signal
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        pass


class Runner:
    def __init__(self, max_workers: int = 16) -> None:
        self.executor = ThreadPoolExecutor(max_workers=max_workers,
                                           thread_name_prefix="runner")
        self._live: dict[str, dict[str, Any]] = {}   # run_id -> 实时缓冲
        self._procs: dict[str, subprocess.Popen] = {}
        self._cancelled: set[str] = set()
        self._active: dict[str, str] = {}            # task_id -> run_id（"" 表示已占位未开始）
        self._lock = threading.Lock()
        threading.Thread(target=self._sweep_live, daemon=True).start()

    # ---------------------------------------------------------------- 入口
    def submit(self, task: dict[str, Any], trigger: str = "manual") -> bool:
        """提交任务执行；同一任务已在运行则直接跳过，返回是否受理。"""
        with self._lock:
            if task["id"] in self._active:
                return False
            # 立即占位，避免与 _execute_once 之间的竞态窗口导致重复并发
            self._active[task["id"]] = ""
        self.executor.submit(self._run_with_retry, task, trigger)
        return True

    def wait_run_id(self, task_id: str, timeout: float = 2.0) -> str:
        """等待并返回刚刚提交的那次运行的 run_id（占位值 "" 视为尚未开始）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                rid = self._active.get(task_id)
            if rid:
                return rid
            time.sleep(0.05)
        return ""

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            run_id = self._active.get(task_id)
        if not run_id:
            return False
        self._cancelled.add(run_id)
        proc = self._procs.get(run_id)
        if proc and proc.poll() is None:
            _kill_tree(proc)
            return True
        return False

    def is_running(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._active

    def running_task_ids(self) -> list[str]:
        with self._lock:
            return [tid for tid, rid in self._active.items() if rid]

    def live_output(self, run_id: str) -> str:
        item = self._live.get(run_id)
        if not item:
            return ""
        if item.get("expires") and item["expires"] <= time.time():
            return ""
        with item["lock"]:
            return "".join(item["chunks"])

    # ------------------------------------------------------------ 执行流程
    def _run_with_retry(self, task: dict[str, Any], trigger: str) -> None:
        """执行任务并在失败时重试。占位由 submit() 负责，此处统一释放。"""
        task_id = task["id"]
        try:
            attempts = max(1, int(task.get("retries") or 0) + 1)
            last = None
            for attempt in range(1, attempts + 1):
                last = self._execute_once(task, trigger, attempt)
                if last["status"] in ("success", "killed"):
                    break
                if attempt < attempts:
                    delay = max(0, int(task.get("retry_delay") or 0))
                    if delay:
                        time.sleep(delay)
            if last is None:
                return
            ok = last["status"] == "success"
            store.touch_task_result(task_id, last["status"], ok)
            try:
                notify_task_result(task.get("notify"), task.get("name", ""), last["status"],
                                   last.get("exit_code"), last.get("duration", 0.0),
                                   last.get("output", ""))
            except Exception:  # noqa: BLE001
                pass
            if ok:
                self._run_after_success(task, last, trigger)
        finally:
            # 整个重试链结束后才释放占位，避免重试间隔内被重复调度
            with self._lock:
                self._active.pop(task_id, None)

    def _run_after_success(self, task: dict[str, Any], last: dict[str, Any],
                           trigger: str = "manual") -> None:
        """任务最终成功后执行用户配置的收尾命令（如关机），分离进程不阻塞。

        仅定时调度（trigger == "schedule"）触发的运行才执行收尾命令；
        手动"立即运行"不触发，避免调试时误关机。
        等待逻辑在独立线程中进行：先等所有其他任务跑完（且近期无任务即将
        定时启动）再触发，避免关机打断同时间段的其他任务（如钉钉推送）。
        """
        if trigger != "schedule":
            return
        after_cmd = (task.get("after_success_cmd") or "").strip()
        if not after_cmd:
            return
        run_id = last.get("id", "")
        threading.Thread(target=self._after_success_worker,
                         args=(task.get("id", ""), run_id, after_cmd),
                         daemon=True).start()

    def _after_success_worker(self, task_id: str, run_id: str, after_cmd: str) -> None:
        """等待所有其他任务执行完毕后，再执行收尾命令。

        阻塞条件（满足任一就继续等）：
        1. 有其他任务正在运行；
        2. 有非高频任务将在 AFTER_UPCOMING_WINDOW 秒内定时启动（等待它跑完）。
        高频任务（运行周期短于该窗口，如每 5 分钟巡检）不阻塞，否则永远等不到空闲。
        总等待超过 AFTER_MAX_WAIT 后强制触发（避免一个挂死的任务让电脑永不关机）。
        """
        from .scheduler import scheduler_manager  # 延迟导入，避免与 scheduler 的循环依赖

        started = time.time()
        note_sent = False
        while True:
            others = [tid for tid in self.running_task_ids() if tid != task_id]
            upcoming = self._upcoming_tasks(scheduler_manager, task_id)
            if not others and not upcoming:
                break
            waited = time.time() - started
            if waited > AFTER_MAX_WAIT:
                store.append_run_output(
                    run_id,
                    f"\n[after-success] 等待其他任务超时({AFTER_MAX_WAIT // 60}分钟)，仍执行收尾命令")
                break
            if not note_sent:
                names = [store.task_name(t) for t in others + upcoming]
                store.append_run_output(
                    run_id,
                    f"\n[after-success] 等待其他任务完成后执行收尾命令，当前等待: "
                    f"{('、'.join(names)) or '(即将调度)'}")
                note_sent = True
            time.sleep(AFTER_POLL_INTERVAL)

        store.append_run_output(run_id, f"\n[after-success] 已触发收尾命令: {after_cmd}")
        cwd = str(BASE_DIR)
        env = os.environ.copy()
        env.update(DEFAULT_ENV)
        try:
            subprocess.Popen(
                after_cmd,
                shell=True,
                cwd=cwd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception:  # noqa: BLE001
            pass

    def _upcoming_tasks(self, scheduler_manager: Any, exclude_task_id: str) -> list[str]:
        """返回即将在 AFTER_UPCOMING_WINDOW 秒内定时启动、且值得等待的任务 id 列表。

        高频任务（两次运行间隔短于该窗口，如每 5 分钟的巡检）会被排除：
        对它们来说"没有任务在跑"的空闲窗口稍纵即逝，等它们只会永远等下去。
        """
        now = datetime.now(timezone.utc)
        result: list[str] = []
        try:
            jobs = scheduler_manager.scheduler.get_jobs()
        except Exception:  # noqa: BLE001
            return result
        for job in jobs:
            if job.id == exclude_task_id or not job.next_run_time:
                continue
            delta = (job.next_run_time - now).total_seconds()
            if not (0 <= delta <= AFTER_UPCOMING_WINDOW):
                continue
            period = self._job_period_seconds(job)
            if period is not None and period < AFTER_FREQUENT_PERIOD:
                continue  # 高频轮询任务不阻塞
            result.append(str(job.id))
        return result

    @staticmethod
    def _job_period_seconds(job: Any) -> float | None:
        """估算作业的运行周期（两次触发间隔秒数）；无法估算返回 None。"""
        try:
            tr = job.trigger
            t1 = tr.get_next_fire_time(None, datetime.now(timezone.utc))
            if not t1:
                return None
            t2 = tr.get_next_fire_time(t1, t1)
            if not t2:
                return None
            return (t2 - t1).total_seconds()
        except Exception:  # noqa: BLE001
            return None

    def _execute_once(self, task: dict[str, Any], trigger: str, attempt: int) -> dict[str, Any]:
        task_id = task["id"]
        command = task.get("command", "")
        cwd = task.get("cwd") or str(BASE_DIR)
        if not os.path.isdir(cwd):
            cwd = str(BASE_DIR)

        env = os.environ.copy()
        env.update(DEFAULT_ENV)
        for k, v in (task.get("env") or {}).items():
            if str(v).strip() != "":
                env[str(k)] = str(v)

        run_id = store.create_run(task_id, task.get("name", ""), trigger, attempt, command, cwd)
        live = {"chunks": [], "lock": threading.Lock(), "expires": None}
        self._live[run_id] = live
        with self._lock:
            self._active[task_id] = run_id

        started = time.time()
        raw_out, raw_err = bytearray(), bytearray()
        try:
            proc = subprocess.Popen(
                command,
                shell=bool(task.get("use_shell", 1)),
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception as exc:  # noqa: BLE001
            duration = time.time() - started
            err = f"{type(exc).__name__}: {exc}"
            store.finish_run(run_id, "failed", None, "", err, duration)
            self._release_run(run_id)
            return {"status": "failed", "exit_code": None, "duration": duration,
                    "output": "", "error": err}

        self._procs[run_id] = proc

        def _pump(stream, sink: bytearray, tag: str, dec):
            """逐块读取（不用 readline：进度条、\\r 刷新等无换行输出会一直缓冲到进程结束）。

            read1 可能在多字节字符中间截断，故用增量解码器保证中文不被切成乱码；
            落库用的完整输出仍走 decode_bytes 的 UTF-8→GBK 兜底解码。
            """
            try:
                while True:
                    chunk = stream.read1(4096)
                    if not chunk:
                        break
                    sink.extend(chunk)
                    if len(sink) > MAX_OUTPUT_BYTES:
                        del sink[: len(sink) - MAX_OUTPUT_BYTES]
                    text = dec.decode(chunk)
                    if not text:
                        continue
                    with live["lock"]:
                        live["chunks"].append(("" if tag == "out" else "[err] ") + text)
            except Exception:  # noqa: BLE001
                pass
            finally:
                try:
                    tail = dec.decode(b"", True)
                    if tail:
                        with live["lock"]:
                            live["chunks"].append(("" if tag == "out" else "[err] ") + tail)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass

        import codecs  # 局部导入，保持模块顶部整洁

        t_out = threading.Thread(
            target=_pump,
            args=(proc.stdout, raw_out, "out",
                  codecs.getincrementaldecoder("utf-8")("replace")),
            daemon=True)
        t_err = threading.Thread(
            target=_pump,
            args=(proc.stderr, raw_err, "err",
                  codecs.getincrementaldecoder("utf-8")("replace")),
            daemon=True)
        t_out.start()
        t_err.start()

        timeout = max(0, int(task.get("timeout") or 0))
        timed_out = False
        try:
            proc.wait(timeout=timeout if timeout > 0 else None)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(proc)

        t_out.join(timeout=5)
        t_err.join(timeout=5)

        duration = time.time() - started
        output = decode_bytes(bytes(raw_out))
        error = decode_bytes(bytes(raw_err))
        exit_code = proc.returncode

        if run_id in self._cancelled:
            status = "killed"
        elif timed_out:
            status = "timeout"
        elif exit_code == 0:
            status = "success"
        else:
            status = "failed"

        store.finish_run(run_id, status, exit_code, output, error, duration)
        self._release_run(run_id)
        return {"id": run_id, "status": status, "exit_code": exit_code,
                "duration": duration, "output": output, "error": error}

    def _release_run(self, run_id: str) -> None:
        """释放进程与实时缓冲。占位 _active 由 _run_with_retry 的 finally 统一释放。"""
        self._procs.pop(run_id, None)
        item = self._live.get(run_id)
        if item is not None:
            # 保留 2 分钟再清，避免前端在任务刚结束时读到空缓冲而输出区闪空
            item["expires"] = time.time() + LIVE_TTL
        self._cancelled.discard(run_id)

    def _sweep_live(self) -> None:
        """后台清理过期的实时输出缓冲。"""
        while True:
            time.sleep(30)
            try:
                now = time.time()
                with self._lock:
                    expired = [rid for rid, it in self._live.items()
                               if it.get("expires") and it["expires"] <= now]
                    for rid in expired:
                        self._live.pop(rid, None)
            except Exception:  # noqa: BLE001
                pass


runner = Runner()
