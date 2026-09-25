"""待复核状态存储：rechecks 表结构与全部读写 SQL。

复核规则见 recheck_rules.py，接口入口见 app.py，三者分开维护。
所有函数接收外层事务的连接，不自行提交。
"""
from __future__ import annotations

import sqlite3
from typing import Any

import recheck_rules

SCHEMA = """
CREATE TABLE IF NOT EXISTS rechecks(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES flight_plans(id),
    plan_revision INTEGER NOT NULL,
    restriction_id INTEGER NOT NULL REFERENCES restrictions(id),
    status TEXT NOT NULL DEFAULT 'pending',
    disposition TEXT,
    reviewer TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
"""

_QUEUE_SQL = """
SELECT r.id AS recheck_id, r.plan_id, r.plan_revision, r.restriction_id, r.created_at,
       p.callsign, p.operator_id, p.starts_at, p.ends_at, p.status AS plan_status, p.revision AS current_revision,
       rs.name AS restriction_name, rs.kind AS restriction_kind, rs.reason AS restriction_reason
FROM rechecks r
JOIN flight_plans p ON p.id = r.plan_id
JOIN restrictions rs ON rs.id = r.restriction_id
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def flag_plan(conn: sqlite3.Connection, plan_id: int, plan_revision: int, restriction_id: int, now: str) -> int:
    cur = conn.execute("INSERT INTO rechecks(plan_id,plan_revision,restriction_id,status,created_at) VALUES(?,?,?,?,?)",
                       (plan_id, plan_revision, restriction_id, recheck_rules.RECHECK_PENDING, now))
    return int(cur.lastrowid)


def pending_for_plan(conn: sqlite3.Connection, plan_id: int) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM rechecks WHERE plan_id=? AND status=? ORDER BY id",
                        (plan_id, recheck_rules.RECHECK_PENDING)).fetchall()
    return [dict(r) for r in rows]


def resolve(conn: sqlite3.Connection, recheck_id: int, status: str, disposition: str, reviewer: str, now: str) -> None:
    conn.execute("UPDATE rechecks SET status=?,disposition=?,reviewer=?,resolved_at=? WHERE id=?",
                 (status, disposition, reviewer, now, recheck_id))


def supersede_plan(conn: sqlite3.Connection, plan_id: int, now: str, disposition: str) -> int:
    """关闭计划所有待复核单（运营方变更退回草稿、计划过期等情况），返回关闭数量。"""
    cur = conn.execute("UPDATE rechecks SET status=?,disposition=?,resolved_at=? WHERE plan_id=? AND status=?",
                       (recheck_rules.RECHECK_SUPERSEDED, disposition, now, plan_id, recheck_rules.RECHECK_PENDING))
    return cur.rowcount


def pending_queue(conn: sqlite3.Connection, operator_id: str | None = None) -> list[dict[str, Any]]:
    sql = _QUEUE_SQL + " WHERE r.status=?"
    params: list[Any] = [recheck_rules.RECHECK_PENDING]
    if operator_id:
        sql += " AND p.operator_id=?"
        params.append(operator_id)
    rows = conn.execute(sql + " ORDER BY r.id", params).fetchall()
    return [dict(r) for r in rows]
