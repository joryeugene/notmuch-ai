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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS corrections (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT NOT NULL,
            message_id  TEXT NOT NULL,
            wrong_tag   TEXT NOT NULL,
            correct_tag TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS triage_reviews (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ts         TEXT NOT NULL,
            message_id TEXT NOT NULL,
            action     TEXT NOT NULL
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


def log_correction(message_id: str, wrong_tag: str, correct_tag: str) -> None:
    """Record a triage correction — user said wrong_tag should have been correct_tag."""
    with closing(_conn()) as conn:
        conn.execute(
            "INSERT INTO corrections (ts, message_id, wrong_tag, correct_tag) VALUES (?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                message_id.lstrip("id:"),
                wrong_tag,
                correct_tag,
            ),
        )
        conn.commit()


def recent_corrections(limit: int = 50) -> list[dict]:
    """Return most recent N triage corrections."""
    with closing(_conn()) as conn:
        rows = conn.execute(
            "SELECT ts, message_id, wrong_tag, correct_tag FROM corrections ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {"ts": r[0], "message_id": r[1], "wrong_tag": r[2], "correct_tag": r[3]}
        for r in rows
    ]


def log_triage_review(message_id: str, action: str) -> None:
    """Record that a message was reviewed in triage (confirmed/corrected/skipped)."""
    with closing(_conn()) as conn:
        conn.execute(
            "INSERT INTO triage_reviews (ts, message_id, action) VALUES (?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                message_id.lstrip("id:"),
                action,
            ),
        )
        conn.commit()


def recent_untriaged(limit: int = 50) -> list[dict]:
    """Return recent non-dry-run decisions not yet reviewed in triage.

    A decision is considered triaged when triage_reviews contains a row for
    that message_id with ts >= the decision's ts.  Re-classified emails get a
    new decision row after the review, so they reappear in the next session.
    """
    with closing(_conn()) as conn:
        rows = conn.execute(
            """SELECT ts, message_id, subject, rule_name, tags_added
               FROM decisions
               WHERE dry_run = 0
                 AND NOT EXISTS (
                     SELECT 1 FROM triage_reviews r
                     WHERE r.message_id = decisions.message_id
                       AND r.ts >= decisions.ts
                 )
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
        }
        for r in rows
    ]


def count_classified() -> int:
    """Return total number of distinct message-ids that have been classified (non-dry-run)."""
    with closing(_conn()) as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT message_id) FROM decisions WHERE dry_run = 0"
        ).fetchone()
    return row[0] if row else 0


def last_run_time() -> str | None:
    """Return ISO timestamp of the most recent non-dry-run classification, or None."""
    with closing(_conn()) as conn:
        row = conn.execute(
            "SELECT MAX(ts) FROM decisions WHERE dry_run = 0"
        ).fetchone()
    return row[0] if row else None


def count_recent_errors() -> int:
    """Return number of error decisions logged in the last 24 hours."""
    cutoff = datetime.now(timezone.utc).isoformat()[:10]  # YYYY-MM-DD
    with closing(_conn()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE rule_name = 'error' AND ts >= ?",
            (cutoff,),
        ).fetchone()
    return row[0] if row else 0


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
