"""钉钉 / 通用 Webhook 通知。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
from typing import Any

import requests


def _sign(secret: str) -> tuple[str, str]:
    timestamp = str(round(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"),
                         digestmod=hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return timestamp, sign


def send_dingtalk(webhook: str, secret: str, title: str, text: str,
                  at_mobiles: list[str] | None = None, at_all: bool = False,
                  timeout: int = 15) -> dict[str, Any]:
    """发送钉钉群机器人消息（支持关键词 / 加签 / IP 段三种安全设置）。"""
    if not webhook:
        return {"ok": False, "error": "未配置 Webhook"}
    url = webhook
    if secret:
        ts, sign = _sign(secret)
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}timestamp={ts}&sign={sign}"

    at = {"atMobiles": at_mobiles or [], "isAtAll": bool(at_all)}
    payload = {
        "msgtype": "markdown",
        "markdown": {"title": title, "text": text},
        "at": at,
    }
    try:
        resp = requests.post(url, json=payload, timeout=timeout,
                             headers={"Content-Type": "application/json; charset=utf-8"})
        data = resp.json()
        ok = data.get("errcode") == 0
        return {"ok": ok, "error": "" if ok else json.dumps(data, ensure_ascii=False)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def build_task_message(task_name: str, status: str, exit_code: int | None,
                       duration: float, output: str, max_chars: int = 1200) -> tuple[str, str]:
    icon = {"success": "✅", "failed": "❌", "timeout": "⏱️", "killed": "🛑"}.get(status, "ℹ️")
    status_cn = {"success": "成功", "failed": "失败", "timeout": "超时",
                 "killed": "已终止", "running": "运行中"}.get(status, status)
    title = f"{icon} 任务{status_cn}：{task_name}"
    body = output.strip() or "（无输出）"
    if len(body) > max_chars:
        body = body[:max_chars] + "\n…（内容已截断）"
    text = (
        f"### {icon} {task_name}\n\n"
        f"**状态**：{status_cn}（退出码 {exit_code}）\n\n"
        f"**耗时**：{duration:.2f} 秒\n\n"
        f"**输出**\n\n```\n{body}\n```"
    )
    return title, text


def notify_task_result(notify: dict[str, Any] | None, task_name: str, status: str,
                       exit_code: int | None, duration: float, output: str) -> dict[str, Any]:
    """按任务配置发送通知；任务未配置钉钉机器人时回退到全局配置。

    优先级：
    1. 任务单独配置了钉钉机器人（type=dingtalk 且填了 webhook）→ 按任务配置发送
    2. 任务显式设置 on=never → 不通知（显式关闭优先于全局）
    3. 未配置 / 配置不全 → 使用全局设置里的机器人（未启用则静默跳过）
    """
    cfg = dict(notify or {})
    if cfg.get("type") == "dingtalk" and cfg.get("on") == "never":
        return {"ok": True, "skipped": True}
    if not (cfg.get("type") == "dingtalk" and (cfg.get("webhook") or "").strip()):
        from .settings import get_dingtalk
        g = get_dingtalk()
        if g.get("enabled") and (g.get("webhook") or "").strip():
            cfg = dict(g, type="dingtalk")
        else:
            return {"ok": True, "skipped": True}
    mode = cfg.get("on") or "failure"
    if mode == "never":
        return {"ok": True, "skipped": True}
    if mode == "failure" and status == "success":
        return {"ok": True, "skipped": True}
    title, text = build_task_message(task_name, status, exit_code, duration, output)
    return send_dingtalk(cfg.get("webhook", ""), cfg.get("secret", ""), title, text,
                         at_mobiles=cfg.get("at_mobiles") or [], at_all=bool(cfg.get("at_all")))
