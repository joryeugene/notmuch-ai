"""
SQLite audit trail for classification decisions.

One job: append log entries. Zero reads during classify.
Schema is append-only — never update, never delete.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


DB_PATH = Path.home() / ".local" / "share" / "notmuch-ai" / "audit.db"


@dataclass
class Decision:
    message_id: str
    subject: str
    from_addr: str
    rule_name: str
    rule_condition: str
    tags_added: list[str]
    tags_removed: list[str]
    llm_response: str | None
    dry_run: bool


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decisions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT NOT NULL,
            message_id  TEXT NOT NULL,
            subject     TEXT,
            from_addr   TEXT,
            rule_name   TEXT,
            rule_cond   TEXT,
            tags_added  TEXT,
            tags_removed TEXT,
            llm_response TEXT,
            dry_run     INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    return conn


def log(decision: Decision) -> None:
    with closing(_conn()) as conn:
        conn.execute(
            """INSERT INTO decisions
               (ts, message_id, subject, from_addr, rule_name, rule_cond,
                tags_added, tags_removed, llm_response, dry_run)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                decision.message_id,
                decision.subject,
                decision.from_addr,
                decision.rule_name,
                decision.rule_condition,
                json.dumps(decision.tags_added),
                json.dumps(decision.tags_removed),
                decision.llm_response,
                1 if decision.dry_run else 0,
            ),
        )
        conn.commit()


def why(message_id: str) -> list[dict]:
    """Return all logged decisions for a message-id, newest first."""
    mid = message_id.lstrip("id:")
    with closing(_conn()) as conn:
        rows = conn.execute(
            """SELECT ts, rule_name, rule_cond, tags_added, tags_removed,
                      llm_response, dry_run
               FROM decisions
               WHERE message_id = ?
               ORDER BY id DESC""",
            (mid,),
        ).fetchall()
    return [
        {
            "ts": r[0],
            "rule": r[1],
            "condition": r[2],
            "tags_added": json.loads(r[3] or "[]"),
            "tags_removed": json.loads(r[4] or "[]"),
            "llm_response": r[5],
            "dry_run": bool(r[6]),
        }
        for r in rows
    ]


def recent(limit: int = 50) -> list[dict]:
    """Return most recent N decisions across all messages."""
    with closing(_conn()) as conn:
        rows = conn.execute(
            """SELECT ts, message_id, subject, rule_name, tags_added, dry_run
               FROM decisions
               ORDER BY id DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [
        {
            "ts": r[0],
            "message_id": r[1],
            "subject": r[2],
            "rule": r[3],
            "tags_added": json.loads(r[4] or "[]"),
            "dry_run": bool(r[5]),
        }
        for r in rows
    ]
