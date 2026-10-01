"""内置任务模板：一键创建常见定时任务（含脚本与参数说明）。"""
from __future__ import annotations

from pathlib import Path

from .config import SCRIPTS_DIR, python_exe

TEMPLATES: list[dict] = [
    {
        "id": "baidu-hot-dingtalk",
        "name": "百度热搜 → 钉钉推送",
        "icon": "🔥",
        "desc": "定时抓取百度热搜榜 Top N，整理成 Markdown 通过钉钉机器人推送到群。",
        "script": "baidu_hot_dingtalk.py",
        "args": "",
        "schedule_type": "cron",
        "schedule_expr": "0 9 * * *",
        "tags": "抓取,钉钉",
        "timeout": 120,
        "fields": [
            {
                "key": "DINGTALK_WEBHOOK", "label": "钉钉机器人 Webhook", "required": True,
                "placeholder": "https://oapi.dingtalk.com/robot/send?access_token=xxxxxxxx",
                "hint": "群设置 → 智能群助手 → 添加机器人 → 自定义",
            },
            {
                "key": "DINGTALK_SECRET", "label": "加签密钥", "required": False,
                "placeholder": "SECxxxxxxxxxxxxxxxx",
                "hint": "安全设置为「加签」时必填；「关键词」或「IP 段」方式留空",
            },
            {
                "key": "HOT_TOPN", "label": "抓取条数", "required": False,
                "placeholder": "10", "hint": "默认 10 条",
            },
            {
                "key": "DINGTALK_KEYWORD", "label": "关键词（安全设置为关键词时）", "required": False,
                "placeholder": "热搜", "hint": "若机器人安全设置为「自定义关键词」，需与此处一致",
            },
        ],
    },
    {
        "id": "python-script-dingtalk",
        "name": "运行 Python 脚本 → 结果推送钉钉",
        "icon": "🐍",
        "desc": "执行你自己的 Python 脚本，把运行结果（stdout）推送到钉钉群。",
        "script": "run_script_dingtalk.py",
        "args": "",
        "schedule_type": "interval",
        "schedule_expr": "3600",
        "tags": "脚本,钉钉",
        "timeout": 1800,
        "fields": [
            {
                "key": "TARGET_SCRIPT", "label": "要运行的脚本路径", "required": True,
                "placeholder": r"D:\code\my_job.py",
                "hint": "绝对路径，参数可直接写在后面，例如 D:\\code\\job.py --mode=fast",
            },
            {
                "key": "DINGTALK_WEBHOOK", "label": "钉钉机器人 Webhook", "required": True,
                "placeholder": "https://oapi.dingtalk.com/robot/send?access_token=xxxxxxxx",
            },
            {
                "key": "DINGTALK_SECRET", "label": "加签密钥", "required": False,
                "placeholder": "SECxxxxxxxxxxxxxxxx",
            },
            {
                "key": "PUSH_ON_SUCCESS", "label": "成功时也推送", "required": False,
                "placeholder": "0", "hint": "填 1 表示成功也推送，留空/0 表示仅失败时推送",
            },
        ],
    },
    {
        "id": "health-check-dingtalk",
        "name": "接口健康巡检 → 异常告警",
        "icon": "🩺",
        "desc": "定时请求指定 URL，状态码异常或响应超时则推送钉钉告警。",
        "script": "health_check_dingtalk.py",
        "args": "",
        "schedule_type": "interval",
        "schedule_expr": "300",
        "tags": "巡检,告警",
        "timeout": 120,
        "fields": [
            {
                "key": "CHECK_URL", "label": "巡检地址", "required": True,
                "placeholder": "https://example.com/health",
            },
            {
                "key": "DINGTALK_WEBHOOK", "label": "钉钉机器人 Webhook", "required": True,
                "placeholder": "https://oapi.dingtalk.com/robot/send?access_token=xxxxxxxx",
            },
            {
                "key": "DINGTALK_SECRET", "label": "加签密钥", "required": False,
                "placeholder": "SECxxxxxxxxxxxxxxxx",
            },
            {
                "key": "EXPECT_STATUS", "label": "期望状态码", "required": False,
                "placeholder": "200", "hint": "多个用逗号分隔，默认 200",
            },
            {
                "key": "MAX_SECONDS", "label": "响应耗时上限（秒）", "required": False,
                "placeholder": "5", "hint": "超过则视为异常",
            },
        ],
    },
]


def get_template(template_id: str) -> dict | None:
    return next((t for t in TEMPLATES if t["id"] == template_id), None)


def build_command(template: dict) -> str:
    script = SCRIPTS_DIR / template["script"]
    py = python_exe()
    args = (" " + template["args"]) if template.get("args") else ""
    return f'"{py}" "{script}"{args}'


def public_templates() -> list[dict]:
    return [
        {k: v for k, v in t.items() if k != "fields"} | {"field_count": len(t.get("fields", []))}
        for t in TEMPLATES
    ]


def ensure_script(template: dict) -> Path:
    path = SCRIPTS_DIR / template["script"]
    if not path.exists():
        raise FileNotFoundError(f"模板脚本缺失: {path}")
    return path
