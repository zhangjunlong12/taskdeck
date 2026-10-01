# -*- coding: utf-8 -*-
"""开机自启动管理 —— 跨平台实现。

平台方案：
- Windows: HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run 注册表键（用户级，无需管理员）
- macOS:   ~/Library/LaunchAgents/com.taskdeck.app.plist（launchd，RunAtLoad）
- Linux:   ~/.config/autostart/taskdeck.desktop（XDG 自启规范）

用法（命令行）：
    python app.py --autostart on      启用
    python app.py --autostart off     关闭
    python app.py --autostart status  查看状态
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from .config import BASE_DIR

APP_PY = BASE_DIR / "app.py"
PLIST_ID = "com.taskdeck.app"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_ID}.plist"
DESKTOP_PATH = Path.home() / ".config" / "autostart" / "taskdeck.desktop"

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "TaskDeck"


def _launch_command(hidden: bool) -> list[str]:
    """返回用于开机自启的命令行（当前解释器 + 入口脚本）。"""
    exe = sys.executable
    if os.name == "nt":
        # Windows 下优先用 pythonw.exe，避免启动时弹出控制台窗口
        pythonw = Path(exe).with_name("pythonw.exe")
        if pythonw.exists():
            exe = str(pythonw)
    args = [exe, str(APP_PY)]
    if hidden:
        args.append("--hidden")
    return args


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------

def _win_set(enable: bool) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0,
                        winreg.KEY_SET_VALUE) as key:
        if enable:
            cmd = subprocess.list2cmdline(_launch_command(hidden=True))
            winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE_NAME)
            except FileNotFoundError:
                pass


def _win_status() -> str | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE_NAME)
            return str(value)
    except FileNotFoundError:
        return None


# ---------------------------------------------------------------------------
# macOS
# ---------------------------------------------------------------------------

def _mac_set(enable: bool) -> None:
    # 先卸载旧的，保证改动生效
    subprocess.run(["launchctl", "unload", str(PLIST_PATH)],
                   capture_output=True, timeout=10)

    if not enable:
        PLIST_PATH.unlink(missing_ok=True)
        return

    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    plist: dict = {
        "Label": PLIST_ID,
        "ProgramArguments": _launch_command(hidden=False),
        "WorkingDirectory": str(BASE_DIR),
        "RunAtLoad": True,
        "KeepAlive": False,
        "ProcessType": "Interactive",
    }
    with open(PLIST_PATH, "wb") as f:
        plistlib.dump(plist, f)
    # 立即加载；失败不致命（下次登录自然生效）
    subprocess.run(["launchctl", "load", str(PLIST_PATH)],
                   capture_output=True, timeout=10)


def _mac_status() -> str | None:
    return f"{PLIST_PATH} (launchd)" if PLIST_PATH.exists() else None


# ---------------------------------------------------------------------------
# Linux
# ---------------------------------------------------------------------------

def _linux_set(enable: bool) -> None:
    if not enable:
        DESKTOP_PATH.unlink(missing_ok=True)
        return
    DESKTOP_PATH.parent.mkdir(parents=True, exist_ok=True)
    exe, app_py = _launch_command(hidden=False)
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=TaskDeck\n"
        f"Exec={exe} {app_py}\n"
        f"Path={BASE_DIR}\n"
        "X-GNOME-Autostart-enabled=true\n"
    )
    DESKTOP_PATH.write_text(content, encoding="utf-8")


def _linux_status() -> str | None:
    return str(DESKTOP_PATH) if DESKTOP_PATH.exists() else None


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------

def set_enabled(enable: bool) -> None:
    if sys.platform.startswith("win"):
        _win_set(enable)
    elif sys.platform == "darwin":
        _mac_set(enable)
    else:
        _linux_set(enable)


def status() -> str:
    """返回描述自启状态的人类可读字符串。"""
    detail = (_win_status() if sys.platform.startswith("win")
              else _mac_status() if sys.platform == "darwin"
              else _linux_status())
    if detail is None:
        return "未启用"
    return f"已启用 → {detail}"
