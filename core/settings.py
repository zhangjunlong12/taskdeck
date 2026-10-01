# -*- coding: utf-8 -*-
"""全局设置 —— JSON 文件存储（data/settings.json）。

当前包含：全局钉钉群机器人。任务未单独配置通知时，自动用它发送任务结果。
"""
from __future__ import annotations

import json
import threading
from typing import Any

from .config import DATA_DIR

SETTINGS_PATH = DATA_DIR / "settings.json"
_lock = threading.Lock()

DEFAULTS: dict[str, Any] = {
    "dingtalk": {
        "enabled": False,     # 是否启用全局通知
        "webhook": "",        # 机器人 Webhook 地址
        "secret": "",         # 加签密钥（SEC 开头，可空）
        "on": "always",       # always=总是通知 | failure=仅失败时通知
        "at_all": False,      # 通知时是否 @所有人
    },
}


def load() -> dict[str, Any]:
    """读取设置；文件不存在或损坏时返回默认值。"""
    with _lock:
        data: dict[str, Any] = {}
        try:
            if SETTINGS_PATH.exists():
                data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            data = {}
        merged = json.loads(json.dumps(DEFAULTS))  # 深拷贝默认值
        for key, sub in data.items():
            if isinstance(sub, dict) and isinstance(merged.get(key), dict):
                merged[key].update(sub)
            else:
                merged[key] = sub
        return merged


def save(data: dict[str, Any]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(SETTINGS_PATH)


def update_dingtalk(patch: dict[str, Any]) -> dict[str, Any]:
    """合并更新全局钉钉配置，返回更新后的完整设置。"""
    data = load()
    d = data.setdefault("dingtalk", {})
    with _lock:
        if "enabled" in patch:
            d["enabled"] = bool(patch["enabled"])
        for key in ("webhook", "secret", "on"):
            if key in patch:
                d[key] = str(patch[key] or "").strip()
        if "at_all" in patch:
            d["at_all"] = bool(patch["at_all"])
        if "on" in d and d["on"] not in ("always", "failure"):
            d["on"] = "always"
        save(data)
    return data


def get_dingtalk() -> dict[str, Any]:
    return load().get("dingtalk") or {}
