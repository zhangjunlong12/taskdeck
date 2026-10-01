"""配置与运行时路径。"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
LOG_DIR = DATA_DIR / "logs"
SCRIPTS_DIR = BASE_DIR / "scripts"
DB_PATH = DATA_DIR / "taskmanager.db"
WEB_DIR = BASE_DIR / "web"
ASSETS_DIR = BASE_DIR / "assets"

APP_NAME = "TaskDeck"
APP_TITLE = "TaskDeck · 定时任务管理器"
VERSION = "1.0.0"

PORT_START = 17823
PORT_SCAN = 30        # 单例检测时扫描的端口范围：17823 ~ 17852

# 默认注入给子进程的环境变量，尽量保证 Python 输出为 UTF-8，避免中文乱码
DEFAULT_ENV = {
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
}


def python_exe() -> str:
    """返回可用于执行子脚本的解释器路径。

    从 pythonw.exe 启动时 sys.executable 是无控制台版本，子脚本的 stdout/stderr
    行为不可靠，故统一回退到同目录下的 python.exe。
    """
    exe = Path(sys.executable)
    candidate = exe.with_name("python.exe")
    if candidate.exists():
        return str(candidate)
    return str(exe)


def ensure_dirs() -> None:
    for p in (DATA_DIR, LOG_DIR, SCRIPTS_DIR, ASSETS_DIR):
        p.mkdir(parents=True, exist_ok=True)


def free_port(start: int | None = None, tries: int = 60) -> int:
    """从 PORT_START 开始找一个可用的本地端口。"""
    start = PORT_START if start is None else start
    for i in range(tries):
        port = start + i
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("没有找到可用端口")
