# -*- coding: utf-8 -*-
"""百度热搜抓取 → 钉钉机器人推送（仅依赖标准库）。

环境变量：
  DINGTALK_WEBHOOK   必填，钉钉自定义机器人 Webhook
  DINGTALK_SECRET    可选，安全设置为「加签」时填写
  DINGTALK_KEYWORD   可选，安全设置为「关键词」时填写（默认：热搜）
  HOT_TOPN           可选，抓取条数，默认 10

命令行参数优先级高于环境变量。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
BAIDU_API = "https://top.baidu.com/api/board?platform=wise&tab=realtime"
BAIDU_PAGE = "https://top.baidu.com/board?tab=realtime"


def http_get(url: str, headers: dict | None = None, timeout: int = 20) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


def fetch_hot(topn: int) -> list[dict]:
    """抓取百度热搜榜，优先使用接口，失败时回退解析页面内嵌 JSON。"""
    try:
        status, text = http_get(BAIDU_API, {"Referer": BAIDU_PAGE})
        data = json.loads(text)
        items: list[dict] = []
        for card in data.get("data", {}).get("cards", []) or []:
            for c in card.get("content", []) or []:
                if not c.get("word"):
                    continue
                items.append({
                    "word": c["word"].strip(),
                    "desc": (c.get("desc") or "").strip().replace("\n", " "),
                    "score": str(c.get("hotScore") or ""),
                    "url": c.get("rawUrl") or c.get("url") or "",
                })
        if items:
            return items[:topn]
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 接口抓取失败：{exc}，尝试解析页面", file=sys.stderr)

    status, html = http_get(BAIDU_PAGE)
    items = []
    for m in re.finditer(r"<!--s-data:(.*?)-->", html, re.S):
        try:
            payload = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        for card in payload.get("data", {}).get("cards", []) or []:
            for c in card.get("content", []) or []:
                if not c.get("word"):
                    continue
                items.append({
                    "word": c["word"].strip(),
                    "desc": (c.get("desc") or "").strip().replace("\n", " "),
                    "score": str(c.get("hotScore") or ""),
                    "url": c.get("rawUrl") or c.get("url") or "",
                })
        if items:
            break
    if not items:
        raise RuntimeError(f"未能解析热搜数据（HTTP {status}）")
    return items[:topn]


def sign_url(webhook: str, secret: str) -> str:
    if not secret:
        return webhook
    ts = str(round(time.time() * 1000))
    digest = hmac.new(secret.encode(), f"{ts}\n{secret}".encode(), hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest))
    sep = "&" if "?" in webhook else "?"
    return f"{webhook}{sep}timestamp={ts}&sign={sign}"


def send_dingtalk(webhook: str, secret: str, title: str, text: str,
                  keyword: str = "") -> bool:
    payload = {
        "msgtype": "markdown",
        "markdown": {"title": title, "text": text},
        "at": {"isAtAll": False},
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        sign_url(webhook, secret), data=body,
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        result = json.loads(resp.read().decode("utf-8", errors="replace"))
    if result.get("errcode") != 0:
        raise RuntimeError(f"钉钉返回错误：{result}")
    return True


def render(items: list[dict], keyword: str) -> tuple[str, str]:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    title = f"{keyword or '百度热搜'} · {now}"
    lines = [f"### 🔥 百度热搜榜 · {now}", ""]
    for i, it in enumerate(items, 1):
        rank = f"**{i}.**"
        link = f" [{it['word']}]({it['url']})" if it["url"] else f" {it['word']}"
        score = f" · 热度 {it['score']}" if it["score"] else ""
        lines.append(f"{rank}{link}{score}")
        if it["desc"]:
            lines.append(f"> {it['desc'][:90]}")
        lines.append("")
    lines.append("---")
    lines.append(f"共 {len(items)} 条 · 由 TaskDeck 定时推送")
    return title, "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="百度热搜 → 钉钉推送")
    ap.add_argument("--topn", type=int, default=int(os.getenv("HOT_TOPN") or 10))
    ap.add_argument("--webhook", default=os.getenv("DINGTALK_WEBHOOK", ""))
    ap.add_argument("--secret", default=os.getenv("DINGTALK_SECRET", ""))
    ap.add_argument("--keyword", default=os.getenv("DINGTALK_KEYWORD", "热搜"))
    ap.add_argument("--dry-run", action="store_true", help="只打印不推送")
    args = ap.parse_args()

    items = fetch_hot(max(1, args.topn))
    for i, it in enumerate(items, 1):
        print(f"{i:>2}. {it['word']}  (热度 {it['score']})")

    if args.dry_run:
        print("\n[dry-run] 未推送")
        return 0

    if not args.webhook:
        print("[error] 缺少 DINGTALK_WEBHOOK，无法推送", file=sys.stderr)
        return 2

    title, text = render(items, args.keyword)
    send_dingtalk(args.webhook, args.secret, title, text)
    print(f"\n[ok] 已推送 {len(items)} 条热搜到钉钉")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as exc:
        print(f"[error] 网络请求失败：{exc}", file=sys.stderr)
        sys.exit(3)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
