# -*- coding: utf-8 -*-
"""TaskDeck 端到端冒烟测试。

用法：
    python tests/smoke_test.py [base_url]
默认 base_url = http://127.0.0.1:17823（自动回退扫描 17823~17852 上已运行的实例）
"""
from __future__ import annotations

import sys
import time

import requests

DEFAULT_PORT = 17823
PORT_SCAN = 30

PASSED, FAILED = 0, 0


def detect_base() -> str:
    """自动探测正在运行的 TaskDeck 实例。"""
    for port in range(DEFAULT_PORT, DEFAULT_PORT + PORT_SCAN):
        url = f"http://127.0.0.1:{port}"
        try:
            resp = requests.get(f"{url}/healthz", timeout=1)
            if resp.status_code == 200 and resp.json().get("app") == "TaskDeck":
                return url
        except Exception:  # noqa: BLE001
            continue
    return f"http://127.0.0.1:{DEFAULT_PORT}"


BASE = sys.argv[1] if len(sys.argv) > 1 else detect_base()


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  [PASS] {name}")
    else:
        FAILED += 1
        print(f"  [FAIL] {name} {extra}")


def wait_run(task_id: str, timeout: int = 40) -> dict:
    """等待任务最近一次运行结束，返回运行记录。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        runs = requests.get(f"{BASE}/api/runs",
                            params={"task_id": task_id, "limit": 1}, timeout=10).json()["items"]
        if runs and runs[0]["status"] != "running":
            return runs[0]
        time.sleep(0.6)
    raise TimeoutError("等待运行结束超时")


def main() -> int:
    print(f"\n=== TaskDeck 冒烟测试 · {BASE} ===\n")

    # 1. 健康检查
    print("[1] 服务健康")
    r = requests.get(f"{BASE}/healthz", timeout=10)
    check("healthz 返回 200", r.status_code == 200 and r.json().get("ok") is True)

    info = requests.get(f"{BASE}/api/system/info", timeout=10).json()["item"]
    check("系统信息可读", bool(info["python"]) and bool(info["base_dir"]))
    print(f"       Python: {info['python']}")

    # 2. 创建任务
    print("\n[2] 任务 CRUD")
    payload = {
        "name": "冒烟测试-输出任务",
        "command": f'"{info["python"]}" -c "print(\'hello taskdeck\'); print(\'第二行\')"',
        "schedule_type": "interval", "schedule_expr": "3600",
        "enabled": 0, "timeout": 30, "retries": 0,
        "tags": "测试", "note": "冒烟测试创建",
    }
    task = requests.post(f"{BASE}/api/tasks", json=payload, timeout=10).json()
    check("创建任务成功", task.get("ok") is True, str(task))
    tid = task["item"]["id"]
    check("调度描述正确", task["item"]["schedule_desc"] == "每小时",
          task["item"].get("schedule_desc"))

    listed = requests.get(f"{BASE}/api/tasks", timeout=10).json()["items"]
    check("任务出现在列表中", any(t["id"] == tid for t in listed))

    updated = requests.put(f"{BASE}/api/tasks/" + tid, json={"timeout": 45}, timeout=10).json()
    check("更新任务成功", updated["item"]["timeout"] == 45)

    # 3. 手动运行
    print("\n[3] 手动运行与输出捕获")
    requests.post(f"{BASE}/api/tasks/{tid}/run", timeout=10)
    run = wait_run(tid)
    check("运行状态为 success", run["status"] == "success", run["status"])
    check("捕获到 stdout", "hello taskdeck" in run["output"], repr(run["output"][:80]))
    check("运行触发器为 manual", run["trigger"] == "manual")
    check("退出码为 0", run["exit_code"] == 0)

    # 4. 失败任务
    print("\n[4] 失败退出码")
    fail = requests.post(f"{BASE}/api/tasks", json={
        "name": "冒烟测试-失败任务",
        "command": f'"{info["python"]}" -c "import sys; sys.stderr.write(\'boom\'); sys.exit(3)"',
        "schedule_type": "interval", "schedule_expr": "3600", "enabled": 0, "timeout": 30,
    }, timeout=10).json()["item"]
    requests.post(f"{BASE}/api/tasks/{fail['id']}/run", timeout=10)
    fr = wait_run(fail["id"])
    check("状态为 failed", fr["status"] == "failed", fr["status"])
    check("退出码为 3", fr["exit_code"] == 3, str(fr["exit_code"]))
    check("捕获到 stderr", "boom" in fr["error"], repr(fr["error"][:60]))

    # 5. 超时终止
    print("\n[5] 超时终止")
    slow = requests.post(f"{BASE}/api/tasks", json={
        "name": "冒烟测试-超时任务",
        "command": f'"{info["python"]}" -c "import time; time.sleep(120)"',
        "schedule_type": "interval", "schedule_expr": "3600", "enabled": 0, "timeout": 3,
    }, timeout=10).json()["item"]
    t0 = time.time()
    requests.post(f"{BASE}/api/tasks/{slow['id']}/run", timeout=10)
    sr = wait_run(slow["id"], timeout=30)
    elapsed = time.time() - t0
    check("状态为 timeout", sr["status"] == "timeout", sr["status"])
    check("在超时后 5 秒内结束", elapsed < 10, f"{elapsed:.1f}s")

    # 6. 环境变量注入
    print("\n[6] 环境变量注入")
    envtask = requests.post(f"{BASE}/api/tasks", json={
        "name": "冒烟测试-环境变量",
        "command": f'"{info["python"]}" -c "import os; print(os.environ.get(\'MY_FLAG\',\'none\'))"',
        "schedule_type": "interval", "schedule_expr": "3600", "enabled": 0,
        "env": {"MY_FLAG": "flag-ok"}, "timeout": 30,
    }, timeout=10).json()["item"]
    requests.post(f"{BASE}/api/tasks/{envtask['id']}/run", timeout=10)
    er = wait_run(envtask["id"])
    check("环境变量已注入子进程", "flag-ok" in er["output"], repr(er["output"][:80]))

    # 7. 调度器同步
    print("\n[7] 调度器同步")
    cron_task = requests.post(f"{BASE}/api/tasks", json={
        "name": "冒烟测试-每天9点",
        "command": f'"{info["python"]}" -c "print(1)"',
        "schedule_type": "cron", "schedule_expr": "0 9 * * *", "enabled": 1, "timeout": 30,
    }, timeout=10).json()["item"]
    check("Cron 描述正确", cron_task["schedule_desc"] == "每天 09:00", cron_task["schedule_desc"])
    check("已生成下次运行时间", bool(cron_task["next_run"]), str(cron_task["next_run"]))
    print(f"       下次运行：{cron_task['next_run']}")

    off = requests.post(f"{BASE}/api/tasks/{cron_task['id']}/enable",
                        json={"enabled": False}, timeout=10).json()["item"]
    check("停用后无下次运行时间", off["next_run"] is None, str(off["next_run"]))

    # 8. 模板
    print("\n[8] 模板库")
    tpls = requests.get(f"{BASE}/api/templates", timeout=10).json()["items"]
    check("模板数量 >= 3", len(tpls) >= 3, str(len(tpls)))
    missing = requests.post(f"{BASE}/api/templates/baidu-hot-dingtalk/create",
                            json={"name": "x", "env": {}}, timeout=10)
    check("缺少必填项时拒绝创建", missing.status_code == 400)
    created = requests.post(f"{BASE}/api/templates/baidu-hot-dingtalk/create",
                            json={"name": "冒烟测试-百度热搜",
                                  "env": {"DINGTALK_WEBHOOK": "https://example.invalid/hook",
                                          "HOT_TOPN": "3"},
                                  "schedule_type": "cron", "schedule_expr": "0 9 * * *"},
                            timeout=10).json()
    check("模板创建任务成功", created.get("ok") is True, str(created)[:200])
    check("命令指向模板脚本", "baidu_hot_dingtalk.py" in created["item"]["command"])
    check("环境变量已写入", created["item"]["env"].get("HOT_TOPN") == "3")

    # 9. 统计
    print("\n[9] 统计接口")
    st = requests.get(f"{BASE}/api/stats", timeout=10).json()["item"]
    check("统计返回 total", isinstance(st["total"], int) and st["total"] > 0)

    # 10. 清理
    print("\n[10] 清理测试数据")
    for t in [tid, fail["id"], slow["id"], envtask["id"], cron_task["id"], created["item"]["id"]]:
        requests.delete(f"{BASE}/api/tasks/{t}", timeout=10)
    left = requests.get(f"{BASE}/api/tasks", timeout=10).json()["items"]
    # 只断言本次测试创建的任务已清干净（用户自建任务可能同时存在）
    leftover = [t["name"] for t in left if t["name"].startswith("冒烟测试")]
    check("测试任务已清理", not leftover, f"残留：{leftover}")

    print(f"\n=== 结果：{PASSED} 通过 / {FAILED} 失败 ===\n")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
