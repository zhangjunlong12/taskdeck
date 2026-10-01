"""端到端自测：验证实时输出、真实抓取、超时终止、失败重试、并发保护、手动停止。

用法：python tests/e2e_test.py [--base http://127.0.0.1:17823]
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  → {detail}" if detail else ""))


def api(base: str, method: str, path: str, payload: dict | None = None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read().decode("utf-8", errors="replace"))


def tasks_by_name(base: str) -> dict[str, dict]:
    return {t["name"]: t for t in api(base, "GET", "/api/tasks")["items"]}


def run_task(base: str, tid: str) -> dict:
    return api(base, "POST", f"/api/tasks/{tid}/run")


def wait_done(base: str, tid: str, run_id: str, timeout: float = 180.0) -> dict:
    """等待任务结束，返回最终的运行记录。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        res = api(base, "GET", f"/api/runs/{run_id}")
        item = res.get("item")
        if item and item["status"] != "running":
            return item
        time.sleep(0.3)
    return api(base, "GET", f"/api/runs/{run_id}").get("item", {})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:17823")
    args = ap.parse_args()
    base = args.base
    tn = tasks_by_name(base)

    print("=" * 70)
    print("TaskDeck 端到端自测")
    print("=" * 70)

    # ---------------------------------------------------------- 1 实时输出
    print("\n[1] 实时输出流式回传与中文编码")
    t = tn["演示 · 实时输出与中文"]
    res = run_task(base, t["id"])
    check("立即运行返回 run_id", bool(res.get("run_id")), str(res.get("run_id")))
    rid = res.get("run_id")
    if rid:
        snaps, last = [], ""
        deadline = time.time() + 20
        while time.time() < deadline:
            item = api(base, "GET", f"/api/runs/{rid}").get("item", {})
            live = item.get("live_output") or ""
            if live != last:
                snaps.append((round(time.time(), 2), live))
                last = live
            if item.get("status") != "running":
                snaps.append((round(time.time(), 2), live))
                break
            time.sleep(0.25)
        # 脚本共 4 行输出、间隔 1 秒，若流式正常应捕捉到 3 个以上不同快照
        check("输出是流式增长而非一次性刷出", len(snaps) >= 3, f"捕获 {len(snaps)} 个快照")
        final = wait_done(base, t["id"], rid)
        check("任务最终成功", final.get("status") == "success", final.get("status"))
        check("中文输出完整无乱码", "中文输出测试" in (final.get("output") or ""),
              (final.get("output") or "").splitlines()[0][:40] if final.get("output") else "")
        check("特殊字符保留", "✅" in (final.get("output") or ""))

    # ---------------------------------------------------------- 2 真实抓取
    print("\n[2] 百度热搜抓取（真实网络）")
    t = tn["百度热搜 → 钉钉推送"]
    res = run_task(base, t["id"])
    rid = res.get("run_id")
    if rid:
        item = wait_done(base, t["id"], rid, timeout=150)
        out = item.get("output") or ""
        check("抓取任务成功", item.get("status") == "success", item.get("status"))
        check("抓到热搜条目", "热度" in out and out.strip().count("\n") >= 5,
              f"{len([l for l in out.splitlines() if l.strip()])} 行输出")
        check("dry-run 未真实推送", "[dry-run] 未推送" in out)

    # ---------------------------------------------------------- 3 健康巡检
    print("\n[3] 接口健康巡检")
    t = tn["接口健康巡检 · 百度"]
    res = run_task(base, t["id"])
    rid = res.get("run_id")
    if rid:
        item = wait_done(base, t["id"], rid, timeout=90)
        check("巡检任务成功", item.get("status") == "success", item.get("status"))

    # ---------------------------------------------------------- 4 超时终止
    print("\n[4] 超时强制终止")
    t = tn["演示 · 超时终止（3 秒超时）"]
    res = run_task(base, t["id"])
    rid = res.get("run_id")
    if rid:
        item = wait_done(base, t["id"], rid, timeout=60)
        check("超时候状态为 timeout", item.get("status") == "timeout", item.get("status"))
        check("耗时接近超时上限（未被拖到 30 秒）", (item.get("duration") or 99) < 15,
              f"{item.get('duration', 0):.1f}s")
        check("未打印不应出现的输出", "never" not in (item.get("output") or ""))

    # ---------------------------------------------------------- 5 失败重试
    print("\n[5] 失败自动重试")
    t = tn["演示 · 失败重试（重试 2 次）"]
    res = run_task(base, t["id"])
    rid = res.get("run_id")
    if rid:
        item = wait_done(base, t["id"], rid, timeout=90)
        # 重试链：失败 → 等 2s → 再试 → 等 2s → 再试，需轮询等它走完
        attempts: list[int] = []
        runs = []
        deadline = time.time() + 30
        while time.time() < deadline:
            runs = api(base, "GET", f"/api/runs?task_id={t['id']}&limit=10")["items"]
            attempts = sorted({r["attempt"] for r in runs[:5]})
            # 重试链跑完（3 次）且都已落定，才停止等待
            if len(attempts) >= 3 and all(r["status"] != "running" for r in runs[:3]):
                break
            time.sleep(0.5)
        runs = api(base, "GET", f"/api/runs?task_id={t['id']}&limit=10")["items"]
        check("产生 3 次尝试（1+2 次重试）", attempts == [1, 2, 3] or len(attempts) >= 3,
              f"attempts={attempts}")
        check("全部以失败结束", all(r["status"] == "failed" for r in runs[:3]),
              str([r["status"] for r in runs[:3]]))
        check("退出码透传为 7", item.get("exit_code") == 7, str(item.get("exit_code")))

    # ---------------------------------------------------------- 6 并发保护
    print("\n[6] 并发保护")
    t = tn["演示 · 实时输出与中文"]
    r1 = run_task(base, t["id"])
    r2 = run_task(base, t["id"])
    check("同一任务并发运行被拒绝", (not r2.get("ok")) and r2.get("error"),
          str(r2.get("error")))
    if r1.get("run_id"):
        wait_done(base, t["id"], r1["run_id"], timeout=30)

    # ---------------------------------------------------------- 7 手动停止
    print("\n[7] 手动终止运行中的任务")
    t = tn["演示 · 超时终止（3 秒超时）"]
    # 临时把超时改大，让它能持续运行以便手动终止
    api(base, "PUT", f"/api/tasks/{t['id']}", {"timeout": 60})
    res = run_task(base, t["id"])
    rid = res.get("run_id")
    if rid:
        time.sleep(1.5)
        stop = api(base, "POST", f"/api/tasks/{t['id']}/stop")
        check("终止指令被受理", bool(stop.get("ok")), str(stop.get("error", "")))
        item = wait_done(base, t["id"], rid, timeout=30)
        check("状态标记为 killed", item.get("status") == "killed", item.get("status"))
        check("远早于 30 秒脚本结束", (item.get("duration") or 99) < 20,
              f"{item.get('duration', 0):.1f}s")
    api(base, "PUT", f"/api/tasks/{t['id']}", {"timeout": 3})

    # ---------------------------------------------------------- 8 统计口径
    print("\n[8] 统计与记录")
    stats = api(base, "GET", "/api/stats")["item"]
    check("今日运行数已累计", stats["today_runs"] > 0, f"today_runs={stats['today_runs']}")
    check("运行中计数归零", stats["running"] == 0, f"running={stats['running']}")

    print("\n" + "=" * 70)
    print(f"结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
    print("=" * 70)
    raise SystemExit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
