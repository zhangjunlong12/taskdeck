# -*- coding: utf-8 -*-
"""TaskDeck · 定时任务管理器 —— 桌面应用入口。

用法：
    python app.py              启动桌面窗口（关闭窗口后最小化到系统托盘）
    python app.py --hidden     启动后直接驻留托盘
    python app.py --no-gui     仅启动本地服务，用浏览器访问
    python app.py --autostart on|off|status   配置/查看开机自启动

跨平台：Windows（WebView2 窗口 + 托盘）、macOS（WKWebView 窗口 + 托盘，
托盘由主循环驱动；关窗即退出）、Linux（--no-gui + 浏览器为主）。
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser

from core import store
from core.config import (APP_NAME, APP_TITLE, ASSETS_DIR, BASE_DIR, PORT_SCAN, PORT_START,
                         SCRIPTS_DIR, VERSION, ensure_dirs, free_port)
from core.scheduler import scheduler_manager
from core.server import FOCUS_HANDLER, create_app

gui_state: dict = {}   # 保存 window / tray icon 引用


# ---------------------------------------------------------------------------
# 图标
# ---------------------------------------------------------------------------

def create_icon_image(size: int = 256):
    """用 Pillow 绘制应用图标，避免打包二进制资源。"""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.24),
                        fill=(37, 99, 235, 255))
    d.ellipse([size * 0.22, size * 0.22, size * 0.78, size * 0.78],
              outline=(255, 255, 255, 255), width=max(2, size // 22))
    cx = cy = size / 2
    d.line([cx, cy, cx, size * 0.34], fill=(255, 255, 255, 255), width=max(2, size // 20))
    d.line([cx, cy, size * 0.68, cy], fill=(255, 255, 255, 255), width=max(2, size // 20))
    return img


def ensure_icon() -> str:
    ensure_dirs()
    png = ASSETS_DIR / "icon.png"
    ico = ASSETS_DIR / "icon.ico"
    if not png.exists():
        create_icon_image(256).save(png)
    if not ico.exists():
        try:
            create_icon_image(128).save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
        except Exception:  # noqa: BLE001
            pass
    return str(ico if ico.exists() else png)


# ---------------------------------------------------------------------------
# 本地服务
# ---------------------------------------------------------------------------

def start_server(port: int | None = None) -> tuple[object, int]:
    app = create_app()
    port = port or free_port()
    threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, debug=False,
                               threaded=True, use_reloader=False),
        daemon=True, name="flask").start()
    for _ in range(80):
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.1)
    return app, port


# ---------------------------------------------------------------------------
# 托盘与窗口
# ---------------------------------------------------------------------------

def setup_tray(window, port: int, icon_path: str) -> None:
    try:
        import pystray
        from PIL import Image
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 托盘不可用：{exc}")
        return

    image = Image.open(icon_path)

    def on_open(icon=None, item=None):
        focus_window(window)

    def on_browser(icon=None, item=None):
        webbrowser.open(f"http://127.0.0.1:{port}/?src=browser")

    def on_folder(icon=None, item=None):
        try:
            if os.name == "nt":
                os.startfile(str(SCRIPTS_DIR))  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(SCRIPTS_DIR)])
            else:
                subprocess.Popen(["xdg-open", str(SCRIPTS_DIR)])
        except Exception:  # noqa: BLE001
            pass

    def on_reload(icon=None, item=None):
        """WebView2 偶尔会渲染僵死，托盘一键重载页面恢复。"""
        try:
            window.evaluate_js("window.__taskdeck_reload && window.__taskdeck_reload()")
        except Exception:  # noqa: BLE001
            try:
                window.load_url(f"http://127.0.0.1:{port}/")
            except Exception:  # noqa: BLE001
                pass

    def on_restart(icon=None, item=None):
        """彻底重启：spawn 新实例后退出当前进程。调度 / 任务配置持久化在 SQLite，新实例无缝接管。"""
        kwargs: dict = {"cwd": str(BASE_DIR), "close_fds": True}
        if os.name == "nt":
            CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            kwargs["creationflags"] = CREATE_NO_WINDOW
        try:
            subprocess.Popen([sys.executable, str(BASE_DIR / "app.py")], **kwargs)
        except Exception:  # noqa: BLE001
            pass
        shutdown()

    def on_quit(icon=None, item=None):
        try:
            icon.stop()
        except Exception:  # noqa: BLE001
            pass
        shutdown()

    menu = pystray.Menu(
        pystray.MenuItem("打开 TaskDeck", on_open, default=True),
        pystray.MenuItem("在浏览器中打开", on_browser),
        pystray.MenuItem("刷新页面", on_reload),
        pystray.MenuItem("重启应用", on_restart),
        pystray.MenuItem("打开脚本目录", on_folder),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(f"{APP_NAME} v{VERSION}", lambda *a: None, enabled=False),
        pystray.MenuItem("退出", on_quit),
    )
    tray = pystray.Icon(APP_NAME, image, APP_TITLE, menu)
    gui_state["tray"] = tray
    if sys.platform == "darwin":
        # macOS 上 Cocoa 要求主线程跑 NSApplication 循环，托盘不能另起线程 run。
        # 用 run_detached（须在主线程调用）把图标注册进 AppKit，由随后的
        # webview.start() 主循环统一驱动，两者共存。
        try:
            tray.run_detached()
            gui_state["tray_ok"] = True
        except Exception as e:  # noqa: BLE001
            print(f"[tray] macOS 托盘启动失败（{e}），退化为窗口+浏览器模式")
            gui_state["tray_ok"] = False
    else:
        threading.Thread(target=tray.run, daemon=True, name="tray").start()
        gui_state["tray_ok"] = True

    def on_closing():
        if gui_state.get("tray_ok"):
            try:
                window.hide()      # 关闭窗口只是最小化到托盘，定时任务继续运行
            except Exception:  # noqa: BLE001
                pass
            return False
        # 无托盘平台兜底：关窗即退出（否则窗口藏起来没人能唤回）
        return True

    window.events.closing += on_closing


def _pick_webview2_runtime() -> None:
    """WebView2 运行时自动更新期间（新旧版本目录并存），新版可能出现
    环境创建静默失败（窗口黑屏、无 msedgewebview2 子进程）。
    此时显式指向旧版运行时；更新完成、旧目录被清理后自动回归默认。"""
    base = r"C:\Program Files (x86)\Microsoft\EdgeWebView\Application"
    try:
        versions = sorted(d for d in os.listdir(base)
                          if d[0].isdigit() and os.path.exists(os.path.join(base, d, "msedgewebview2.exe")))
    except OSError:
        return
    if len(versions) >= 2 and os.environ.get("WEBVIEW2_BROWSER_EXECUTABLE_FOLDER") is None:
        old = os.path.join(base, versions[0])
        os.environ["WEBVIEW2_BROWSER_EXECUTABLE_FOLDER"] = old
        print(f"[webview2] 检测到运行时更新中（{' / '.join(versions)}），使用旧版：{versions[0]}")


def run_gui(port: int, icon_path: str, start_hidden: bool = False) -> None:
    import webview

    _pick_webview2_runtime()

    # WebView2 在部分显卡（如 RTX 50 系 + 特定驱动）上 GPU 进程会渲染失败，
    # 窗口表现为一片空白但进程健在。禁用 GPU 硬件加速可根治（界面是纯 HTML，
    # 无视频/3D 场景，CPU 软渲染的性能完全够用）。
    # 也可用环境变量 TASKDECK_ENABLE_GPU=1 恢复硬件加速做对照试验。
    if os.environ.get("TASKDECK_ENABLE_GPU") != "1":
        existing = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")
        if "--disable-gpu" not in existing:
            os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
                (existing + " " if existing else "") + "--disable-gpu"
            )

    window = webview.create_window(
        APP_TITLE,
        url=f"http://127.0.0.1:{port}/?src=app-window",
        width=1400,
        height=900,
        min_size=(1100, 700),
        background_color="#F4F5F7",
        text_select=True,
    )
    gui_state["window"] = window
    FOCUS_HANDLER["fn"] = lambda: focus_window(window)
    setup_tray(window, port, icon_path)

    if start_hidden:
        if gui_state.get("tray_ok"):
            threading.Thread(target=lambda: (time.sleep(1.5), window.hide()),
                             daemon=True).start()
        else:
            print("[warn] 托盘不可用，忽略 --hidden（否则窗口藏起来没人能唤回）")

    webview.start(icon=icon_path, private_mode=False, debug=False)


def focus_window(window) -> None:
    """把窗口显示到前台（托盘唤回 / 单例唤醒都会调用）。"""
    try:
        window.show()
        window.restore()
    except Exception:  # noqa: BLE001
        pass
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        from ctypes import wintypes

        u = ctypes.windll.user32
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd, _l):
            if u.GetWindowTextLengthW(hwnd) > 0:
                buf = ctypes.create_unicode_buffer(256)
                u.GetWindowTextW(hwnd, buf, 256)
                if APP_NAME in buf.value:
                    u.ShowWindow(hwnd, 9)          # SW_RESTORE
                    u.SetForegroundWindow(hwnd)
                    return False
            return True

        u.EnumWindows(EnumWindowsProc(cb), 0)
    except Exception:  # noqa: BLE001
        pass


def find_existing_instance() -> str | None:
    """扫描候选端口，找到已在运行的 TaskDeck 实例。"""
    import json
    import urllib.request

    for port in range(PORT_START, PORT_START + PORT_SCAN):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=0.4) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if data.get("app") == APP_NAME:
            return f"http://127.0.0.1:{port}"
    return None


def focus_existing(base: str) -> bool:
    """通知已有实例显示窗口。"""
    import json
    import urllib.request

    req = urllib.request.Request(base + "/api/system/focus", data=b"{}",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return bool(json.loads(r.read().decode("utf-8")).get("ok"))
    except Exception:  # noqa: BLE001
        return False


def shutdown(code: int = 0) -> None:
    scheduler_manager.shutdown()
    try:
        tray = gui_state.get("tray")
        if tray:
            tray.stop()
    except Exception:  # noqa: BLE001
        pass
    os._exit(code)


def maintenance_loop() -> None:
    """每小时检查一次，清理过期的历史运行记录。"""
    while True:
        time.sleep(3600)
        try:
            store.prune_runs(keep_per_task=200)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--no-gui", action="store_true", help="不打开窗口，仅启动本地服务")
    parser.add_argument("--port", type=int, default=0, help="指定服务端口")
    parser.add_argument("--hidden", action="store_true", help="启动后直接最小化到托盘")
    parser.add_argument("--autostart", choices=["on", "off", "status"], metavar="on|off|status",
                        help="配置开机自启动（Windows 注册表 / macOS LaunchAgent / Linux XDG）")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {VERSION}")
    args = parser.parse_args(argv)

    if args.autostart:
        from core import autostart
        if args.autostart == "on":
            autostart.set_enabled(True)
        elif args.autostart == "off":
            autostart.set_enabled(False)
        print(f"开机自启动：{autostart.status()}")
        return 0

    ensure_dirs()
    store.init_db()

    # 单例：已经有一个实例在跑就直接唤醒它的窗口，避免重复启动
    if not args.no_gui:
        existing = find_existing_instance()
        if existing:
            print(f"TaskDeck 已在运行（{existing}），正在唤醒窗口…")
            if not focus_existing(existing):
                print("[warn] 唤醒失败：该实例可能运行在无窗口模式")
                return 1
            return 0

    icon_path = ensure_icon()
    scheduler_manager.start()
    threading.Thread(target=maintenance_loop, daemon=True, name="maintenance").start()

    _, port = start_server(args.port or None)
    scheduler_manager.sync_all()

    print(f"{APP_NAME} v{VERSION} 已启动 → http://127.0.0.1:{port}/")
    print(f"项目目录：{BASE_DIR}")

    if args.no_gui:
        print("无窗口模式运行中，按 Ctrl+C 退出。")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            shutdown(0)
        return 0

    run_gui(port, icon_path, start_hidden=args.hidden)
    shutdown(0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
