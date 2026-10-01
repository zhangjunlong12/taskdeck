"""SQLite 存储层：任务表 + 运行记录表。"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from typing import Any

from .config import DB_PATH, ensure_dirs

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    command       TEXT NOT NULL,
    cwd           TEXT DEFAULT '',
    use_shell     INTEGER DEFAULT 1,
    env           TEXT DEFAULT '{}',
    schedule_type TEXT DEFAULT 'interval',
    schedule_expr TEXT DEFAULT '3600',
    enabled       INTEGER DEFAULT 1,
    timeout       INTEGER DEFAULT 300,
    retries       INTEGER DEFAULT 0,
    retry_delay   INTEGER DEFAULT 30,
    notify        TEXT DEFAULT '{}',
    tags          TEXT DEFAULT '',
    note          TEXT DEFAULT '',
    after_success_cmd TEXT DEFAULT '',
    created_at    REAL,
    updated_at    REAL,
    last_run_at   REAL,
    last_status   TEXT DEFAULT '',
    run_count     INTEGER DEFAULT 0,
    fail_count    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS runs (
    id         TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL,
    task_name  TEXT DEFAULT '',
    status     TEXT DEFAULT 'running',
    trigger    TEXT DEFAULT 'schedule',
    attempt    INTEGER DEFAULT 1,
    command    TEXT DEFAULT '',
    cwd        TEXT DEFAULT '',
    start_at   REAL,
    end_at     REAL,
    duration   REAL DEFAULT 0,
    exit_code  INTEGER,
    output     TEXT DEFAULT '',
    error      TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_runs_task ON runs(task_id, start_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_start ON runs(start_at DESC);
"""

TASK_FIELDS = [
    "id", "name", "command", "cwd", "use_shell", "env", "schedule_type",
    "schedule_expr", "enabled", "timeout", "retries", "retry_delay",
    "notify", "tags", "note", "after_success_cmd", "created_at", "updated_at",
    "last_run_at", "last_status", "run_count", "fail_count",
]

EDITABLE_FIELDS = [
    "name", "command", "cwd", "use_shell", "schedule_type", "schedule_expr",
    "enabled", "timeout", "retries", "retry_delay", "tags", "note",
    "after_success_cmd",
]


def _connect() -> sqlite3.Connection:
    ensure_dirs()
    c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


def init_db() -> None:
    with _lock, _connect() as c:
        c.executescript(SCHEMA)
        # 轻量迁移：老库补列（CREATE TABLE IF NOT EXISTS 不会更新已存在的表）
        cols = {r["name"] for r in c.execute("PRAGMA table_info(tasks)")}
        if "after_success_cmd" not in cols:
            c.execute("ALTER TABLE tasks ADD COLUMN after_success_cmd TEXT DEFAULT ''")


def new_id(prefix: str = "t") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _decode_json(raw: Any, default: Any) -> Any:
    if raw in (None, "", "null"):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def _row_to_task(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["env"] = _decode_json(d.get("env"), {})
    d["notify"] = _decode_json(d.get("notify"), {})
    d["use_shell"] = int(d.get("use_shell") or 0)
    d["enabled"] = int(d.get("enabled") or 0)
    for k in ("timeout", "retries", "retry_delay", "run_count", "fail_count"):
        d[k] = int(d.get(k) or 0)
    return d


def _row_to_run(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


# --------------------------------------------------------------------------
# 任务
# --------------------------------------------------------------------------

def list_tasks(keyword: str = "", status: str = "") -> list[dict[str, Any]]:
    sql = "SELECT * FROM tasks"
    where, args = [], []
    if keyword:
        where.append("(name LIKE ? OR command LIKE ? OR tags LIKE ?)")
        args += [f"%{keyword}%"] * 3
    if status == "enabled":
        where.append("enabled = 1")
    elif status == "disabled":
        where.append("enabled = 0")
    elif status == "failed":
        where.append("last_status IN ('failed','timeout','killed')")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC"
    with _lock, _connect() as c:
        return [_row_to_task(r) for r in c.execute(sql, args)]


def get_task(task_id: str) -> dict[str, Any] | None:
    with _lock, _connect() as c:
        row = c.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_task(row) if row else None


def task_name(task_id: str) -> str:
    """按 id 取任务名；查不到时回退为 id 本身。"""
    task = get_task(task_id)
    return task["name"] if task else task_id


def create_task(data: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    task = {
        "id": new_id(),
        "name": (data.get("name") or "").strip() or "未命名任务",
        "command": (data.get("command") or "").strip(),
        "cwd": (data.get("cwd") or "").strip(),
        "use_shell": int(data.get("use_shell", 1)),
        "env": data.get("env") or {},
        "schedule_type": data.get("schedule_type") or "interval",
        "schedule_expr": str(data.get("schedule_expr") or "3600").strip(),
        "enabled": int(data.get("enabled", 1)),
        "timeout": int(data.get("timeout", 300) or 0),
        "retries": int(data.get("retries", 0) or 0),
        "retry_delay": int(data.get("retry_delay", 30) or 0),
        "notify": data.get("notify") or {},
        "tags": (data.get("tags") or "").strip(),
        "note": (data.get("note") or "").strip(),
        "after_success_cmd": (data.get("after_success_cmd") or "").strip(),
        "created_at": now,
        "updated_at": now,
        "last_run_at": None,
        "last_status": "",
        "run_count": 0,
        "fail_count": 0,
    }
    cols = ", ".join(task.keys())
    marks = ", ".join("?" * len(task))
    values = [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
              for v in task.values()]
    with _lock, _connect() as c:
        c.execute(f"INSERT INTO tasks ({cols}) VALUES ({marks})", values)
    return task


def update_task(task_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    task = get_task(task_id)
    if not task:
        return None
    sets, values = [], []
    for field in EDITABLE_FIELDS:
        if field not in data:
            continue
        v = data[field]
        if field in ("use_shell", "enabled", "timeout", "retries", "retry_delay"):
            v = int(v or 0)
        elif field in ("name", "command", "cwd", "tags", "note", "after_success_cmd"):
            v = (v or "").strip()
            if field == "command":
                v = v.strip()
        elif field == "schedule_expr":
            v = str(v or "").strip()
        sets.append(f"{field} = ?")
        values.append(v)
    for field in ("env", "notify"):
        if field in data:
            sets.append(f"{field} = ?")
            values.append(json.dumps(data[field] or {}, ensure_ascii=False))
    if not sets:
        return task
    sets.append("updated_at = ?")
    values.append(time.time())
    values.append(task_id)
    with _lock, _connect() as c:
        c.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", values)
    return get_task(task_id)


def set_enabled(task_id: str, enabled: bool) -> dict[str, Any] | None:
    with _lock, _connect() as c:
        c.execute("UPDATE tasks SET enabled = ?, updated_at = ? WHERE id = ?",
                  (1 if enabled else 0, time.time(), task_id))
    return get_task(task_id)


def delete_task(task_id: str) -> bool:
    with _lock, _connect() as c:
        cur = c.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        c.execute("DELETE FROM runs WHERE task_id = ?", (task_id,))
    return cur.rowcount > 0


def touch_task_result(task_id: str, status: str, ok: bool) -> None:
    with _lock, _connect() as c:
        if ok:
            c.execute("UPDATE tasks SET last_run_at=?, last_status=?, run_count=run_count+1 WHERE id=?",
                      (time.time(), status, task_id))
        else:
            c.execute("UPDATE tasks SET last_run_at=?, last_status=?, run_count=run_count+1, "
                      "fail_count=fail_count+1 WHERE id=?", (time.time(), status, task_id))


# --------------------------------------------------------------------------
# 运行记录
# --------------------------------------------------------------------------

def create_run(task_id: str, task_name: str, trigger: str, attempt: int,
               command: str, cwd: str) -> str:
    rid = new_id("r")
    now = time.time()
    with _lock, _connect() as c:
        c.execute(
            "INSERT INTO runs (id, task_id, task_name, status, trigger, attempt, command, cwd, "
            "start_at, end_at, duration, exit_code, output, error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, task_id, task_name, "running", trigger, attempt, command, cwd, now, None, 0, None, "", ""),
        )
    return rid


def finish_run(run_id: str, status: str, exit_code: int | None,
               output: str, error: str, duration: float) -> None:
    with _lock, _connect() as c:
        c.execute(
            "UPDATE runs SET status=?, exit_code=?, output=?, error=?, end_at=?, duration=? WHERE id=?",
            (status, exit_code, output, error, time.time(), duration, run_id),
        )


def append_run_output(run_id: str, text: str) -> None:
    """在运行记录的输出末尾追加一行（用于记录收尾动作等事件）。"""
    if not run_id:
        return
    with _lock, _connect() as c:
        c.execute(
            "UPDATE runs SET output = output || ? WHERE id = ?",
            (text, run_id),
        )


def get_run(run_id: str) -> dict[str, Any] | None:
    with _lock, _connect() as c:
        row = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return _row_to_run(row) if row else None


def list_runs(task_id: str = "", limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    sql = "SELECT * FROM runs"
    args: list[Any] = []
    if task_id:
        sql += " WHERE task_id = ?"
        args.append(task_id)
    sql += " ORDER BY start_at DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    with _lock, _connect() as c:
        rows = c.execute(sql, args).fetchall()
    return [_row_to_run(r) for r in rows]


def count_running() -> int:
    with _lock, _connect() as c:
        return int(c.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[0])


def stats() -> dict[str, Any]:
    # 用本地时区的当天 0 点，避免硬编码 UTC 偏移在非东八区机器上统计错位
    now = datetime.now()
    today_start = datetime(now.year, now.month, now.day).timestamp()
    with _lock, _connect() as c:
        total = c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        enabled = c.execute("SELECT COUNT(*) FROM tasks WHERE enabled=1").fetchone()[0]
        running = c.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[0]
        today = c.execute("SELECT COUNT(*) FROM runs WHERE start_at >= ?", (today_start,)).fetchone()[0]
        today_fail = c.execute(
            "SELECT COUNT(*) FROM runs WHERE start_at >= ? AND status IN ('failed','timeout','killed')",
            (today_start,)).fetchone()[0]
        last = c.execute("SELECT task_name, status, start_at FROM runs ORDER BY start_at DESC LIMIT 1").fetchone()
    return {
        "total": total,
        "enabled": enabled,
        "running": running,
        "today_runs": today,
        "today_failed": today_fail,
        "last": dict(last) if last else None,
    }


def prune_runs(keep_per_task: int = 200) -> int:
    """每个任务只保留最近 keep_per_task 条记录，返回删除条数。"""
    deleted = 0
    with _lock, _connect() as c:
        ids = [r[0] for r in c.execute("SELECT DISTINCT task_id FROM runs")]
        for tid in ids:
            cur = c.execute(
                "DELETE FROM runs WHERE task_id=? AND id NOT IN "
                "(SELECT id FROM runs WHERE task_id=? ORDER BY start_at DESC LIMIT ?)",
                (tid, tid, keep_per_task))
            deleted += cur.rowcount
    return deleted
