# -*- coding: utf-8 -*-
"""HTTP 接口健康巡检 → 异常时推送钉钉告警（仅依赖标准库）。

环境变量：
  CHECK_URL         必填，巡检地址
  DINGTALK_WEBHOOK  必填，钉钉机器人 Webhook
  DINGTALK_SECRET   可选，加签密钥
  EXPECT_STATUS     可选，期望状态码，默认 200（多个用逗号分隔）
  MAX_SECONDS       可选，响应耗时上限（秒），默认 5
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime


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


def main() -> int:
    ap = argparse.ArgumentParser(description="接口健康巡检")
    ap.add_argument("--url", default=os.getenv("CHECK_URL", ""))
    ap.add_argument("--webhook", default=os.getenv("DINGTALK_WEBHOOK", ""))
    ap.add_argument("--secret", default=os.getenv("DINGTALK_SECRET", ""))
    ap.add_argument("--expect", default=os.getenv("EXPECT_STATUS", "200"))
    ap.add_argument("--max-seconds", type=float, default=float(os.getenv("MAX_SECONDS") or 5))
    args = ap.parse_args()

    if not args.url:
        print("[error] 缺少 CHECK_URL", file=sys.stderr)
        return 2

    expect = {int(x) for x in args.expect.replace(" ", "").split(",") if x}
    problems, code, elapsed = [], None, 0.0
    started = time.time()
    try:
        req = urllib.request.Request(args.url, headers={"User-Agent": "TaskDeck-HealthCheck/1.0"})
        with urllib.request.urlopen(req, timeout=max(1.0, args.max_seconds + 5)) as resp:
            code = resp.status
            resp.read(2048)
    except urllib.error.HTTPError as exc:
        code = exc.code
    except Exception as exc:  # noqa: BLE001
        problems.append(f"请求异常：{type(exc).__name__}: {exc}")
    finally:
        elapsed = time.time() - started

    if code is not None and code not in expect:
        problems.append(f"状态码 {code}，期望 {'/'.join(map(str, sorted(expect)))}")
    if code is not None and elapsed > args.max_seconds:
        problems.append(f"响应耗时 {elapsed:.2f}s，超过上限 {args.max_seconds:g}s")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if problems:
        title = f"🚨 接口异常：{args.url}"
        text = (f"### 🚨 接口巡检异常\n\n**地址**：`{args.url}`\n\n"
                f"**状态码**：{code}\n\n**耗时**：{elapsed:.2f} 秒\n\n"
                f"**时间**：{now}\n\n**问题**\n\n"
                + "\n\n".join(f"- {p}" for p in problems))
        print(f"[alert] {'; '.join(problems)}")
        if not args.webhook:
            print("[error] 缺少 DINGTALK_WEBHOOK，无法告警", file=sys.stderr)
            return 2
        send_dingtalk(args.webhook, args.secret, title, text)
        print("[ok] 已推送告警到钉钉")
        return 1

    print(f"[ok] {args.url} 正常 · HTTP {code} · {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"[error] {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
