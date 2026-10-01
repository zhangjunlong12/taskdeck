"""本地 HTTP API 服务。"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from . import store, templates
from .config import APP_NAME, BASE_DIR, SCRIPTS_DIR, VERSION, WEB_DIR
from .runner import runner
from .scheduler import describe_schedule, scheduler_manager

# 由 app.py 注入：把主窗口唤到前台的回调（用于「单例唤醒」）
FOCUS_HANDLER: dict = {"fn": None}


def task_view(task: dict) -> dict:
    view = dict(task)
    view["next_run"] = scheduler_manager.next_run(task["id"])
    view["running"] = runner.is_running(task["id"])
    view["schedule_desc"] = describe_schedule(task["schedule_type"], task["schedule_expr"])
    return view


def run_view(run: dict) -> dict:
    view = dict(run)
    if view.get("status") == "running":
        view["live_output"] = runner.live_output(view["id"])
        view["duration"] = round(time.time() - (view.get("start_at") or time.time()), 2)
    return view


def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path="")
    app.config["JSON_AS_ASCII"] = False
    app.json.ensure_ascii = False

    # -------------------------------------------------- 客户端轮询探针
    # 记录最近 30 秒内每个 User-Agent 的请求次数，用于诊断
    # 「窗口看起来正常但按钮没反应」——若某窗口 UA 计数为 0，说明其页面 JS 已僵死。
    _poll_hits: dict[str, list[float]] = {}
    _poll_lock = threading.Lock()

    @app.before_request
    def _track_poll():
        if request.path.startswith("/api/") or request.path == "/healthz":
            src = request.headers.get("X-TaskDeck-Client")
            ua = src or ((request.headers.get("User-Agent") or "unknown")[:60])
            now = time.time()
            with _poll_lock:
                hits = _poll_hits.setdefault(ua, [])
                hits.append(now)
                # 只保留最近 30 秒，防止无限增长
                _poll_hits[ua] = [t for t in hits if now - t <= 30]

    def poll_stats() -> dict[str, int]:
        now = time.time()
        with _poll_lock:
            return {ua: len([t for t in hits if now - t <= 30])
                    for ua, hits in _poll_hits.items() if hits}

    # ------------------------------------------------------------ 页面
    @app.get("/")
    def index():
        return send_from_directory(str(WEB_DIR), "index.html")

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True, "app": APP_NAME, "version": VERSION,
                        "pollers": poll_stats()})

    # ------------------------------------------------------------ 任务
    @app.get("/api/tasks")
    def api_list_tasks():
        tasks = store.list_tasks(request.args.get("keyword", ""),
                                 request.args.get("status", ""))
        return jsonify({"ok": True, "items": [task_view(t) for t in tasks]})

    @app.post("/api/tasks")
    def api_create_task():
        data = request.get_json(force=True, silent=True) or {}
        if not (data.get("command") or "").strip():
            return jsonify({"ok": False, "error": "命令不能为空"}), 400
        task = store.create_task(data)
        scheduler_manager.sync(task)
        return jsonify({"ok": True, "item": task_view(task)})

    @app.get("/api/tasks/<task_id>")
    def api_get_task(task_id: str):
        task = store.get_task(task_id)
        if not task:
            return jsonify({"ok": False, "error": "任务不存在"}), 404
        return jsonify({"ok": True, "item": task_view(task)})

    @app.put("/api/tasks/<task_id>")
    def api_update_task(task_id: str):
        data = request.get_json(force=True, silent=True) or {}
        if not store.get_task(task_id):
            return jsonify({"ok": False, "error": "任务不存在"}), 404
        if "command" in data and not str(data["command"]).strip():
            return jsonify({"ok": False, "error": "命令不能为空"}), 400
        task = store.update_task(task_id, data)
        scheduler_manager.sync(task)
        return jsonify({"ok": True, "item": task_view(task)})

    @app.delete("/api/tasks/<task_id>")
    def api_delete_task(task_id: str):
        runner.cancel(task_id)
        scheduler_manager.remove(task_id)
        ok = store.delete_task(task_id)
        return jsonify({"ok": ok})

    @app.post("/api/tasks/<task_id>/enable")
    def api_enable_task(task_id: str):
        data = request.get_json(force=True, silent=True) or {}
        task = store.set_enabled(task_id, bool(data.get("enabled", True)))
        if not task:
            return jsonify({"ok": False, "error": "任务不存在"}), 404
        scheduler_manager.sync(task)
        return jsonify({"ok": True, "item": task_view(task)})

    @app.post("/api/tasks/<task_id>/run")
    def api_run_task(task_id: str):
        task = store.get_task(task_id)
        if not task:
            return jsonify({"ok": False, "error": "任务不存在"}), 404
        if runner.is_running(task_id):
            return jsonify({"ok": False, "error": "任务正在运行中"}), 409
        if not runner.submit(task, trigger="manual"):
            return jsonify({"ok": False, "error": "任务已在队列中"}), 409
        # 拿到 run_id 让前端立即跳到本次运行的输出，不必等下一轮轮询
        return jsonify({"ok": True, "run_id": runner.wait_run_id(task_id)})

    @app.post("/api/tasks/<task_id>/stop")
    def api_stop_task(task_id: str):
        ok = runner.cancel(task_id)
        return jsonify({"ok": ok, "error": "" if ok else "任务当前未在运行"})

    # ------------------------------------------------------------ 运行记录
    @app.get("/api/runs")
    def api_list_runs():
        limit = min(int(request.args.get("limit", 60)), 500)
        runs = store.list_runs(request.args.get("task_id", ""), limit=limit)
        return jsonify({"ok": True, "items": [run_view(r) for r in runs]})

    @app.get("/api/runs/<run_id>")
    def api_get_run(run_id: str):
        run = store.get_run(run_id)
        if not run:
            return jsonify({"ok": False, "error": "记录不存在"}), 404
        return jsonify({"ok": True, "item": run_view(run)})

    @app.delete("/api/runs")
    def api_clear_runs():
        import sqlite3
        from .config import DB_PATH
        with sqlite3.connect(DB_PATH) as c:
            if request.args.get("task_id"):
                c.execute("DELETE FROM runs WHERE task_id=?", (request.args["task_id"],))
            else:
                c.execute("DELETE FROM runs")
        return jsonify({"ok": True})

    @app.get("/api/stats")
    def api_stats():
        return jsonify({"ok": True, "item": store.stats()})

    # ------------------------------------------------------------ 模板
    @app.get("/api/templates")
    def api_templates():
        return jsonify({"ok": True, "items": templates.public_templates()})

    @app.get("/api/templates/<template_id>")
    def api_template_detail(template_id: str):
        tpl = templates.get_template(template_id)
        if not tpl:
            return jsonify({"ok": False, "error": "模板不存在"}), 404
        return jsonify({"ok": True, "item": tpl})

    @app.post("/api/templates/<template_id>/create")
    def api_template_create(template_id: str):
        tpl = templates.get_template(template_id)
        if not tpl:
            return jsonify({"ok": False, "error": "模板不存在"}), 404
        data = request.get_json(force=True, silent=True) or {}
        env = {str(k): str(v).strip() for k, v in (data.get("env") or {}).items()
               if str(v).strip() != ""}
        missing = [f["key"] for f in tpl.get("fields", [])
                   if f.get("required") and not env.get(f["key"])]
        if missing and not data.get("force"):
            return jsonify({"ok": False, "error": f"请填写必填项：{', '.join(missing)}",
                            "missing": missing}), 400
        try:
            templates.ensure_script(tpl)
        except FileNotFoundError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

        task = store.create_task({
            "name": data.get("name") or tpl["name"],
            "command": templates.build_command(tpl),
            "cwd": str(BASE_DIR),
            "use_shell": 1,
            "env": env,
            "schedule_type": data.get("schedule_type") or tpl["schedule_type"],
            "schedule_expr": str(data.get("schedule_expr") or tpl["schedule_expr"]),
            "timeout": int(data.get("timeout") or tpl.get("timeout") or 300),
            "retries": int(data.get("retries") or 1),
            "retry_delay": int(data.get("retry_delay") or 60),
            "tags": tpl.get("tags", ""),
            "note": tpl["desc"],
            "enabled": 1 if data.get("enabled", True) else 0,
        })
        scheduler_manager.sync(task)
        return jsonify({"ok": True, "item": task_view(task)})

    # ------------------------------------------------------------ 工具
    @app.get("/api/settings")
    def api_settings_get():
        from .settings import load
        return jsonify({"ok": True, "item": load()})

    @app.put("/api/settings")
    def api_settings_put():
        from .settings import update_dingtalk
        data = request.get_json(force=True, silent=True) or {}
        try:
            item = update_dingtalk(data.get("dingtalk") or {})
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 500
        return jsonify({"ok": True, "item": item})

    @app.post("/api/settings/test")
    def api_settings_test():
        """用设置弹窗里当前填写的（或已保存的全局）钉钉配置发测试消息。"""
        from .settings import get_dingtalk
        from .notify import send_dingtalk
        data = request.get_json(force=True, silent=True) or {}
        g = get_dingtalk()
        webhook = (data.get("webhook") or g.get("webhook") or "").strip()
        secret = (data.get("secret") or g.get("secret") or "").strip()
        if not webhook:
            return jsonify({"ok": False, "error": "未填写 Webhook"}), 400
        res = send_dingtalk(webhook, secret, "TaskDeck 测试消息",
                            "### ✅ TaskDeck 连通性测试\n\n全局钉钉机器人配置正确。")
        return jsonify({"ok": bool(res.get("ok")), "error": res.get("error", "")})

    @app.post("/api/notify/test")
    def api_notify_test():
        from .notify import send_dingtalk
        data = request.get_json(force=True, silent=True) or {}
        res = send_dingtalk(data.get("webhook", ""), data.get("secret", ""),
                            "TaskDeck 测试消息",
                            "### ✅ TaskDeck 连通性测试\n\n如果你看到这条消息，说明钉钉机器人配置正确。")
        return jsonify({"ok": bool(res.get("ok")), "error": res.get("error", "")})

    @app.post("/api/system/open")
    def api_system_open():
        data = request.get_json(force=True, silent=True) or {}
        target = (data.get("path") or "").strip()
        if not target:
            return jsonify({"ok": False, "error": "路径为空"}), 400
        path = Path(target)
        if not path.exists():
            return jsonify({"ok": False, "error": f"路径不存在：{target}"}), 400
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path))  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 500
        return jsonify({"ok": True})

    @app.post("/api/system/focus")
    def api_system_focus():
        """把主窗口显示到前台（供「单例唤醒」使用）。"""
        handler = FOCUS_HANDLER.get("fn")
        if not handler:
            return jsonify({"ok": False, "error": "当前实例无窗口"}), 409
        try:
            handler()
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 500
        return jsonify({"ok": True})

    @app.get("/api/system/info")
    def api_system_info():
        return jsonify({"ok": True, "item": {
            "app": APP_NAME,
            "version": VERSION,
            "python": sys.executable,
            "python_version": sys.version.split()[0],
            "platform": sys.platform,
            "base_dir": str(BASE_DIR),
            "scripts_dir": str(SCRIPTS_DIR),
            "db_path": str(store.DB_PATH),
        }})

    return app
