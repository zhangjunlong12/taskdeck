"""APScheduler 封装：把任务同步成调度作业，并提供人类可读的调度描述。"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.util import astimezone

from . import store
from .runner import runner

LOCAL_TZ = astimezone(None) if astimezone(None) else None

WEEK_CN = {"mon": "周一", "tue": "周二", "wed": "周三", "thu": "周四",
           "fri": "周五", "sat": "周六", "sun": "周日", "*": "每天"}


def seconds_human(n: int) -> str:
    n = max(1, int(n))
    if n < 60:
        return f"每 {n} 秒"
    if n < 3600:
        return f"每 {n // 60} 分钟" if n % 60 == 0 else f"每 {n} 秒"
    if n < 86400:
        return "每小时" if n == 3600 else f"每 {n / 3600:g} 小时"
    return "每天" if n == 86400 else f"每 {n / 86400:g} 天"


def describe_schedule(schedule_type: str, expr: str) -> str:
    expr = (expr or "").strip()
    if schedule_type == "interval":
        try:
            return seconds_human(int(expr))
        except ValueError:
            return f"每 {expr} 秒"
    if schedule_type == "once":
        return f"{expr.replace('T', ' ')} 执行一次"
    if schedule_type == "cron":
        return _describe_cron(expr)
    return expr or "—"


def _describe_cron(expr: str) -> str:
    parts = expr.split()
    if len(parts) == 6:      # 带秒：秒 分 时 日 月 周
        parts = parts[1:]
    if len(parts) != 5:
        return expr
    minute, hour, dom, month, dow = [p.strip() for p in parts]

    def _list(v: str) -> list[str]:
        return [x for x in v.split(",") if x]

    def _fixed(v: str) -> str | None:
        return v if re.fullmatch(r"\d{1,2}", v) else None

    hm = ""
    if (m := _fixed(minute)) is not None:
        if (h := _fixed(hour)) is not None:
            hm = f"{int(h):02d}:{int(m):02d}"
        elif hour == "*":
            hm = f"每小时第 {int(m)} 分"

    if dow != "*":
        days = "/".join(WEEK_CN.get(d.lower()[:3], d) for d in _list(dow))
        return f"{days} {hm}".strip() if hm else f"{days}（{expr}）"
    if dom != "*":
        return f"每月 {dom} 日 {hm}".strip() if hm else f"每月 {dom} 日"
    if month != "*":
        return f"每年 {month} 月 {hm}".strip()
    if hm:
        return f"每天 {hm}" if ":" in hm else hm
    if minute.startswith("*/"):
        return f"每 {minute[2:]} 分钟"
    return expr


def build_trigger(schedule_type: str, expr: str):
    if schedule_type == "interval":
        seconds = int(float(expr or 60))
        return IntervalTrigger(seconds=max(1, seconds), timezone=LOCAL_TZ)
    if schedule_type == "cron":
        return CronTrigger.from_crontab(expr, timezone=LOCAL_TZ)
    if schedule_type == "once":
        dt = datetime.fromisoformat(expr)
        if dt.tzinfo is None and LOCAL_TZ is not None:
            dt = LOCAL_TZ.localize(dt)
        return DateTrigger(run_date=dt)
    raise ValueError(f"不支持的调度类型: {schedule_type}")


class SchedulerManager:
    def __init__(self) -> None:
        self.scheduler = BackgroundScheduler(
            timezone=LOCAL_TZ,
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
        )

    def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.start()

    def shutdown(self) -> None:
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            pass

    def _job(self, task_id: str) -> None:
        task = store.get_task(task_id)
        if not task or not task.get("enabled"):
            return
        runner.submit(task, trigger="schedule")

    def sync(self, task: dict[str, Any]) -> None:
        task_id = task["id"]
        existing = self.scheduler.get_job(task_id)
        if not task.get("enabled"):
            if existing:
                existing.remove()
            return
        try:
            trigger = build_trigger(task["schedule_type"], task["schedule_expr"])
        except Exception:  # noqa: BLE001
            if existing:
                existing.remove()
            return
        if existing:
            existing.reschedule(trigger)
        else:
            self.scheduler.add_job(self._job, trigger=trigger, id=task_id,
                                   args=[task_id], replace_existing=True)

    def remove(self, task_id: str) -> None:
        job = self.scheduler.get_job(task_id)
        if job:
            job.remove()

    def next_run(self, task_id: str) -> str | None:
        job = self.scheduler.get_job(task_id)
        if job and job.next_run_time:
            return job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
        return None

    def sync_all(self) -> None:
        for task in store.list_tasks():
            try:
                self.sync(task)
            except Exception:  # noqa: BLE001
                continue


scheduler_manager = SchedulerManager()
