"""创建一组演示/自测任务，覆盖各典型场景。

用法：python tests/create_demo_tasks.py [--base http://127.0.0.1:17823]
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import SCRIPTS_DIR, python_exe  # noqa: E402

PY = python_exe()


def api(base: str, method: str, path: str, payload: dict | None = None) -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code} {path}: {body}") from exc


def build_tasks() -> list[dict]:
    baidu = SCRIPTS_DIR / "baidu_hot_dingtalk.py"
    health = SCRIPTS_DIR / "health_check_dingtalk.py"

    return [
        {
            "name": "百度热搜 → 钉钉推送",
            "command": f'"{PY}" "{baidu}" --topn 10 --dry-run',
            "cwd": str(SCRIPTS_DIR.parent),
            "use_shell": 1,
            "schedule_type": "cron",
            "schedule_expr": "0 9 * * *",
            "enabled": 1,
            "timeout": 120,
            "retries": 1,
            "retry_delay": 60,
            "env": {"DINGTALK_WEBHOOK": "", "DINGTALK_SECRET": "", "HOT_TOPN": "10"},
            "tags": "抓取,钉钉",
            "note": "每天 9:00 抓取百度热搜 Top10 推送到钉钉群。\n"
                    "【启用推送】填入 DINGTALK_WEBHOOK，并把命令里的 --dry-run 去掉。",
        },
        {
            "name": "接口健康巡检 · 百度",
            "command": f'"{PY}" "{health}" --url https://www.baidu.com --max-seconds 5',
            "cwd": str(SCRIPTS_DIR.parent),
            "use_shell": 1,
            "schedule_type": "interval",
            "schedule_expr": "300",
            "enabled": 1,
            "timeout": 60,
            "retries": 1,
            "retry_delay": 30,
            "env": {"DINGTALK_WEBHOOK": ""},
            "tags": "巡检,告警",
            "note": "每 5 分钟巡检一次；接口异常时推送钉钉告警（需填 DINGTALK_WEBHOOK）。",
        },
        {
            "name": "演示 · 实时输出与中文",
            "command": f'"{PY}" "{SCRIPTS_DIR / "demo_stream.py"}"',
            "cwd": str(SCRIPTS_DIR.parent),
            "use_shell": 1,
            "schedule_type": "interval",
            "schedule_expr": "3600",
            "enabled": 0,
            "timeout": 60,
            "retries": 0,
            "retry_delay": 10,
            "env": {},
            "tags": "演示",
            "note": "验证实时输出流式回传与中文编码，默认停用，点「立即运行」测试。",
        },
        {
            "name": "演示 · 超时终止（3 秒超时）",
            "command": f'"{PY}" "{SCRIPTS_DIR / "demo_timeout.py"}"',
            "cwd": str(SCRIPTS_DIR.parent),
            "use_shell": 1,
            "schedule_type": "interval",
            "schedule_expr": "3600",
            "enabled": 0,
            "timeout": 3,
            "retries": 0,
            "retry_delay": 10,
            "env": {},
            "tags": "演示",
            "note": "脚本要跑 30 秒，超时设 3 秒 → 应被强制终止，状态 timeout。默认停用。",
        },
        {
            "name": "演示 · 失败重试（重试 2 次）",
            "command": f'"{PY}" "{SCRIPTS_DIR / "demo_fail.py"}"',
            "cwd": str(SCRIPTS_DIR.parent),
            "use_shell": 1,
            "schedule_type": "interval",
            "schedule_expr": "3600",
            "enabled": 0,
            "timeout": 30,
            "retries": 2,
            "retry_delay": 2,
            "env": {},
            "tags": "演示",
            "note": "脚本固定 exit 7 → 应自动重试，共产生 attempt 1/2/3 三条记录。默认停用。",
        },
    ]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:17823")
    args = ap.parse_args()

    existing = {t["name"]: t for t in api(args.base, "GET", "/api/tasks")["items"]}
    created, skipped = [], []
    for spec in build_tasks():
        if spec["name"] in existing:
            tid = existing[spec["name"]]["id"]
            api(args.base, "PUT", f"/api/tasks/{tid}", spec)
            skipped.append(spec["name"])
            continue
        res = api(args.base, "POST", "/api/tasks", spec)
        created.append((spec["name"], res["item"]["id"]))

    print(f"新建 {len(created)} 个，更新 {len(skipped)} 个已存在任务")
    for name, tid in created:
        print(f"  + {name}  ({tid})")
    for name in skipped:
        print(f"  = {name}  (已存在，已同步配置)")


if __name__ == "__main__":
    main()
