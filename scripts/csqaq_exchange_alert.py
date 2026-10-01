# -*- coding: utf-8 -*-
"""CSQAQ 挂刀行情监控 → 折扣过低时钉钉报警（仅依赖标准库）。

数据源：CSQAQ 官方开放 API（免费注册 https://csqaq.com 获取 API_TOKEN，
文档 https://docs.csqaq.com/api-187131823 ）。

判定逻辑（对应网站 https://csqaq.com/exchange 显示的"比例"）：
    比例 = 平台售价 / 到手Steam余额 = 1 / max_price
    API 返回的 max_price 即"到手Steam余额 / 平台售价"（收益倍率），
    比例越低越划算（花 1 元平台价换更多 Steam 余额）。
    当 比例 < 阈值（默认 0.4）时推送钉钉。

默认筛选（可用参数覆盖）：
    我想获得 Steam余额(res=0) · 出售方案 Steam丢求购(sort_by=1)
    平台 BUFF+悠悠(platforms=BUFF-YYYP) · 价格 1~5000 · 日成交量 > 50

防刷屏：同一饰品 12 小时内只报一次（状态存 data/csqaq_alert_state.json）。
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
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
API_URL = "https://api.csqaq.com/api/v1/info/exchange_detail"
BIND_IP_URL = "https://api.csqaq.com/api/v1/sys/bind_local_ip"
PAGE_URL = "https://csqaq.com/exchange"
BASE = Path(__file__).resolve().parent.parent
STATE_PATH = BASE / "data" / "csqaq_alert_state.json"
SETTINGS_PATH = BASE / "data" / "settings.json"

# 各平台在响应里的售价字段名（与 platforms 参数对应）
PLATFORM_FIELDS = {
    "BUFF": "buff_sell_price",
    "YYYP": "yyyp_sell_price",
}
STEAM_FEE = 0.87  # Steam 卖出到手比例（网站口径）：到手 = steam_buy_price * 0.87


def http_post_json(url: str, payload: dict, headers: dict | None = None,
                   timeout: int = 25) -> tuple[int, str]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


def fetch_page(token: str, args: dict, page: int) -> list[dict]:
    """调用官方 API 拉取一页挂刀行情。

    处理两类异常：
    - IP 白名单过期（401）→ 自动重绑本机 IP 后重试
    - 429 限频 → 指数退避重试（本机出口 IP 共享，限额易被占用）
    """
    payload = {
        "page_index": page,
        "res": args["res"],
        "platforms": args["platforms"],
        "sort_by": args["sort_by"],
        "min_price": args["min_price"],
        "max_price": args["max_price"],
        "turnover": args["turnover"],
    }
    last_err: Exception | None = None
    for attempt in range(4):
        try:
            status, text = http_post_json(API_URL, payload, {"ApiToken": token})
            data = json.loads(text)
            if status == 200 and data.get("code") == 200:
                return data.get("data") or []
            raise RuntimeError(f"API 返回异常 http={status} code={data.get('code')} "
                               f"msg={data.get('msg')}")
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", "replace")
                data = json.loads(body)
            except Exception:  # noqa: BLE001
                data = {"code": exc.code, "msg": str(exc)}
            status, msg = exc.code, str(data.get("msg") or "")
            # 负责人电脑是动态宽带 IP：账号绑定的 IP 过期时自动重绑一次再重试
            if "IP" in msg and ("绑定" in msg or "不符" in msg):
                print(f"[info] 检测到 IP 白名单过期（{msg.strip()[:80]}），自动重新绑定...")
                if bind_local_ip(token):
                    time.sleep(3)
                    continue
                last_err = RuntimeError("IP 重绑失败")
            elif exc.code == 429 or "429" in str(data.get("code")):
                wait = 40 * (attempt + 1)
                print(f"[warn] 429 限频，等待 {wait}s 后重试（第{attempt + 1}/4次）...")
                time.sleep(wait)
                last_err = RuntimeError("API 限频 429，重试后仍失败")
            else:
                raise RuntimeError(f"API 返回异常 http={status} code={data.get('code')} "
                                   f"msg={msg or data.get('msg')}") from exc
        except urllib.error.URLError as exc:
            last_err = exc
            print(f"[warn] 网络异常（第{attempt + 1}/4次）：{exc}，30s 后重试...")
            time.sleep(30)
    raise RuntimeError(f"拉取失败：{last_err}")


def bind_local_ip(token: str) -> bool:
    """绑定当前出口 IP 到 Token 白名单（官方限频 30 秒/次）。"""
    for attempt in range(2):
        try:
            _, text = http_post_json(BIND_IP_URL, {}, {"ApiToken": token})
            result = json.loads(text)
            print(f"[info] 绑定结果：{result.get('msg')}")
            return result.get("code") == 200
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            print(f"[warn] 绑定请求失败（第{attempt + 1}次）：{exc}")
            if attempt == 0:
                time.sleep(35)
    return False


def ratio_of(item: dict) -> float:
    """计算网站显示口径的"比例"= 平台售价/到手Steam余额（越低越划算）。"""
    mp = item.get("max_price")
    try:
        if mp and float(mp) > 0:
            return 1.0 / float(mp)
    except (TypeError, ValueError):
        pass
    # 兜底：手动计算
    sells = [float(item[k]) for k in PLATFORM_FIELDS.values()
             if item.get(k) not in (None, 0)]
    buy = float(item.get("steam_buy_price") or 0)
    if not sells or buy <= 0:
        return 9.9
    return min(sells) / (buy * STEAM_FEE)


def best_platform(item: dict) -> tuple[str, float]:
    """返回（平台名, 最低平台售价）。"""
    best_name, best_price = "", None
    for name, field in PLATFORM_FIELDS.items():
        v = item.get(field)
        if v not in (None, 0) and (best_price is None or float(v) < best_price):
            best_name, best_price = name, float(v)
    return best_name or "-", best_price or 0.0


def load_state(ttl_hours: float) -> dict[str, float]:
    """读取已报警记录，顺手清理过期条目。"""
    state: dict[str, float] = {}
    try:
        if STATE_PATH.exists():
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        state = {}
    cutoff = time.time() - ttl_hours * 3600
    return {k: v for k, v in state.items() if v >= cutoff}


def save_state(state: dict[str, float]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_PATH)


def sign_url(webhook: str, secret: str) -> str:
    if not secret:
        return webhook
    ts = str(round(time.time() * 1000))
    sign = base64.b64encode(hmac.new(secret.encode(), f"{ts}\n{secret}".encode(),
                                     hashlib.sha256).digest()).decode()
    sep = "&" if "?" in webhook else "?"
    return f"{webhook}{sep}timestamp={ts}&sign={urllib.parse.quote_plus(sign)}"


def send_dingtalk(webhook: str, secret: str, title: str, text: str,
                  at_all: bool = False) -> None:
    payload = {"msgtype": "markdown", "markdown": {"title": title, "text": text},
               "at": {"isAtAll": bool(at_all)}}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(sign_url(webhook, secret), data=body,
                                 headers={"Content-Type": "application/json; charset=utf-8",
                                          "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        result = json.loads(resp.read().decode("utf-8", errors="replace"))
    if result.get("errcode") != 0:
        raise RuntimeError(f"钉钉返回错误：{result}")


def resolve_dingtalk(args) -> tuple[str, str, bool]:
    """钉钉配置优先级：命令行/环境变量 > TaskDeck 全局设置(data/settings.json)。"""
    webhook = args.webhook
    secret = args.secret
    at_all = args.at_all
    if not webhook:
        try:
            g = json.loads(SETTINGS_PATH.read_text(encoding="utf-8")).get("dingtalk") or {}
        except Exception:  # noqa: BLE001
            g = {}
        if g.get("enabled") and g.get("webhook"):
            webhook = g["webhook"]
            secret = secret or g.get("secret", "")
            at_all = at_all or bool(g.get("at_all"))
    return webhook, secret, at_all


def render(hits: list[dict], threshold: float) -> tuple[str, str]:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    title = f"CSQAQ 挂刀折扣警报 · {now}"
    lines = [f"### 🦆 CSQAQ 挂刀折扣警报 · {now}", "",
             f"**筛选条件**：比例 < {threshold} · 平台 BUFF+悠悠 · 日成交 > 50 · 价格 1~5000",
             ""]
    for i, it in enumerate(hits, 1):
        name, price = best_platform(it)
        ratio = it["_ratio"]
        to_hand = float(it.get("steam_buy_price") or 0) * STEAM_FEE
        lines.append(
            f"**{i}. {it.get('name', it.get('market_hash_name', '?'))}**（{name}）  ")
        lines.append(
            f"> 平台售价 {price} → Steam求购 {it.get('steam_buy_price')} "
            f"→ 到手余额 **{to_hand:.3f}**  ")
        lines.append(
            f"> **比例 {ratio:.3f}** · 日成交 {it.get('turnover_number', '?')}  ")
    lines.append("")
    lines.append(f"共 {len(hits)} 件 · 详情 [{PAGE_URL}]({PAGE_URL}) · TaskDeck 推送")
    return title, "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="CSQAQ 挂刀行情折扣监控 → 钉钉警报")
    ap.add_argument("--token", default=os.getenv("CSQAQ_API_TOKEN", ""),
                    help="CSQAQ 开放 API 的 API_TOKEN")
    ap.add_argument("--threshold", type=float, default=0.4,
                    help="比例阈值，低于它才报警（默认 0.4）")
    ap.add_argument("--pages", type=int, default=2, help="抓取页数（默认 2，首页比例最低）")
    ap.add_argument("--platforms", default="BUFF-YYYP")
    ap.add_argument("--min-price", type=float, default=1)
    ap.add_argument("--max-price", type=float, default=5000)
    ap.add_argument("--turnover", type=int, default=50, help="日成交量下限（默认 50）")
    ap.add_argument("--res", type=int, default=0, help="0=获取Steam余额 1=平台余额")
    ap.add_argument("--sort-by", type=int, default=1, help="0=Steam挂底价 1=Steam丢求购")
    ap.add_argument("--ttl", type=float, default=12, help="同一饰品报警冷却小时数")
    ap.add_argument("--webhook", default=os.getenv("DINGTALK_WEBHOOK", ""))
    ap.add_argument("--secret", default=os.getenv("DINGTALK_SECRET", ""))
    ap.add_argument("--at-all", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不推送、不记状态")
    args = ap.parse_args()

    cfg = {"res": args.res, "sort_by": args.sort_by, "platforms": args.platforms,
           "min_price": args.min_price, "max_price": args.max_price,
           "turnover": args.turnover}

    if not args.token:
        print("[error] 缺少 API_TOKEN：--token 或环境变量 CSQAQ_API_TOKEN",
              file=sys.stderr)
        return 2

    # 拉取数据（页间留间隔，官方限频 1 次/秒且本机出口 IP 共享，宁慢勿封）
    items: list[dict] = []
    for page in range(1, max(1, args.pages) + 1):
        page_items = fetch_page(args.token, cfg, page)
        if not page_items:
            break
        items.extend(page_items)
        if page < args.pages:
            time.sleep(6)
    print(f"[info] 拉取 {len(items)} 件饰品（筛选：比例<{args.threshold} "
          f"平台{args.platforms} 日成交>{args.turnover} 价格{args.min_price}~{args.max_price}）")

    # 过滤 + 去重
    for it in items:
        it["_ratio"] = ratio_of(it)
    state = {} if args.dry_run else load_state(args.ttl)
    hits = []
    for it in sorted(items, key=lambda x: x["_ratio"]):
        if it["_ratio"] >= args.threshold:
            continue
        key = str(it.get("id") or it.get("market_hash_name"))
        if key in state:
            continue
        state[key] = time.time()
        hits.append(it)

    if not hits:
        best = min((it["_ratio"] for it in items), default=9.9)
        print(f"[ok] 无低于 {args.threshold} 的饰品（当前最低比例 {best:.3f}），不推送")
        if not args.dry_run:
            save_state(state)
        return 0

    for it in hits:
        name, price = best_platform(it)
        print(f"ALERT {it.get('name', it.get('market_hash_name'))} | 平台 {name} "
              f"售价 {price} | 比例 {it['_ratio']:.3f} | 日成交 {it.get('turnover_number')}")

    if args.dry_run:
        print("\n[dry-run] 未推送、未记录状态")
        return 0

    webhook, secret, at_all = resolve_dingtalk(args)
    if not webhook:
        print("[error] 缺少钉钉 webhook（参数未给且全局机器人未启用）", file=sys.stderr)
        save_state(state)  # 仍记录，避免反复扫到
        return 2

    title, text = render(hits, args.threshold)
    send_dingtalk(webhook, secret, title, text, at_all)
    save_state(state)
    print(f"\n[ok] 已推送 {len(hits)} 件低折扣饰品到钉钉")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        print(f"[error] HTTP {exc.code}：{detail or exc.reason}", file=sys.stderr)
        sys.exit(4)
    except urllib.error.URLError as exc:
        print(f"[error] 网络请求失败：{exc}", file=sys.stderr)
        sys.exit(3)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
