# -*- coding: utf-8 -*-
"""运行指定 Python 脚本 → 把结果推送到钉钉（仅依赖标准库）。

环境变量：
  TARGET_SCRIPT     必填，要执行的脚本路径（可带参数）
  DINGTALK_WEBHOOK  必填，钉钉机器人 Webhook
  DINGTALK_SECRET   可选，加签密钥
  PUSH_ON_SUCCESS   可选，1 表示成功也推送（默认仅失败推送）
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.parse
import urllib.request


def sign_url(webhook: str, secret: str) -> str:
    if not secret:
        return webhook
    ts = str(round(time.time() * 1000))
    digest = hmac.new(secret.encode(), f"{ts}\n{secret}".encode(), hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest))
    sep = "&" if "?" in webhook else "?"
    return f"{webhook}{sep}timestamp={ts}&sign={sign}"


def send_dingtalk(webhook: str, secret: str, title: str, text: str) -> None:
    payload = {"msgtype": "markdown", "markdown": {"title": title, "text": text},
               "at": {"isAtAll": False}}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        sign_url(webhook, secret), data=body,
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        result = json.loads(resp.read().decode("utf-8", errors="replace"))
    if result.get("errcode") != 0:
        raise RuntimeError(f"钉钉返回错误：{result}")


def clip(text: str, limit: int = 1500) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "\n…（已截断）"


def main() -> int:
    ap = argparse.ArgumentParser(description="运行脚本并把结果推送钉钉")
    ap.add_argument("script", nargs="?", default=os.getenv("TARGET_SCRIPT", ""))
    ap.add_argument("--webhook", default=os.getenv("DINGTALK_WEBHOOK", ""))
    ap.add_argument("--secret", default=os.getenv("DINGTALK_SECRET", ""))
    ap.add_argument("--timeout", type=int, default=int(os.getenv("RUN_TIMEOUT") or 1800))
    ap.add_argument("--push-on-success", default=os.getenv("PUSH_ON_SUCCESS", "0"))
    args = ap.parse_args()

    if not args.script:
        print("[error] 缺少 TARGET_SCRIPT", file=sys.stderr)
        return 2

    parts = shlex.split(args.script, posix=(os.name != "nt"))
    if not parts:
        print("[error] TARGET_SCRIPT 为空", file=sys.stderr)
        return 2
    if parts[0] == "python" or parts[0].endswith(".py"):
        if not parts[0].endswith(".py"):
            parts = [sys.executable] + parts[1:]

    started = time.time()
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    proc = subprocess.run(parts, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          env=env, timeout=args.timeout)
    duration = time.time() - started

    sys.stdout.write(proc.stdout or "")
    if proc.stderr:
        sys.stderr.write(proc.stderr)

    ok = proc.returncode == 0
    status_cn = "✅ 成功" if ok else "❌ 失败"
    title = f"{status_cn}：{os.path.basename(parts[-1])}"
    body = clip(proc.stdout if ok else (proc.stderr or proc.stdout))
    text = (f"### {title}\n\n**脚本**：`{' '.join(parts)}`\n\n"
            f"**退出码**：{proc.returncode}\n\n**耗时**：{duration:.2f} 秒\n\n"
            f"**输出**\n\n```\n{body or '（无输出）'}\n```")

    if ok and args.push_on_success not in ("1", "true", "True", "yes"):
        print(f"\n[ok] 脚本执行成功（{duration:.2f}s），未推送（PUSH_ON_SUCCESS=0）")
        return 0
    if not args.webhook:
        print("[error] 缺少 DINGTALK_WEBHOOK，无法推送", file=sys.stderr)
        return 2
    send_dingtalk(args.webhook, args.secret, title, text)
    print("\n[ok] 已推送结果到钉钉")
    return proc.returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.TimeoutExpired:
        print("[error] 脚本执行超时", file=sys.stderr)
        sys.exit(124)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
