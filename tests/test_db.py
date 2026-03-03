"""Unit tests for db.py — uses a tmp path to avoid touching real audit.db."""

from __future__ import annotations

import pytest
from pathlib import Path

import notmuch_ai.db as db_module
from notmuch_ai.db import Decision, log, why, recent


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Redirect DB_PATH to a temp directory for each test."""
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test_audit.db")


def _decision(**kwargs) -> Decision:
    defaults = dict(
        message_id="test123",
        subject="Test subject",
        from_addr="sender@example.com",
        rule_name="built-in: needs-reply",
        rule_condition="a real person wrote this",
        tags_added=["needs-reply"],
        tags_removed=[],
        llm_response="Looks like a direct ask",
        dry_run=False,
    )
    return Decision(**{**defaults, **kwargs})


def test_log_and_why():
    d = _decision()
    log(d)
    results = why("test123")
    assert len(results) == 1
    assert results[0]["rule"] == "built-in: needs-reply"
    assert results[0]["tags_added"] == ["needs-reply"]
    assert results[0]["dry_run"] is False


def test_why_strips_id_prefix():
    log(_decision(message_id="abc456"))
    results = why("id:abc456")
    assert len(results) == 1


def test_why_unknown_message():
    assert why("nonexistent") == []


def test_multiple_decisions_same_message():
    log(_decision(rule_name="rule-1", tags_added=["needs-reply"]))
    log(_decision(rule_name="rule-2", tags_added=["ai-urgent"]))
    results = why("test123")
    assert len(results) == 2
    # Newest first
    assert results[0]["rule"] == "rule-2"


def test_recent_returns_latest_first():
    for i in range(5):
        log(_decision(message_id=f"msg{i}", rule_name=f"rule-{i}"))
    results = recent(limit=3)
    assert len(results) == 3
    assert results[0]["rule"] == "rule-4"


def test_recent_respects_limit():
    for i in range(10):
        log(_decision(message_id=f"msg{i}"))
    results = recent(limit=3)
    assert len(results) == 3


def test_dry_run_flag_preserved():
    log(_decision(dry_run=True))
    results = why("test123")
    assert results[0]["dry_run"] is True


def test_empty_tags_lists():
    log(_decision(tags_added=[], tags_removed=[]))
    results = why("test123")
    assert results[0]["tags_added"] == []
    assert results[0]["tags_removed"] == []


def test_db_created_automatically(tmp_path, monkeypatch):
    nested = tmp_path / "deep" / "nested" / "audit.db"
    monkeypatch.setattr(db_module, "DB_PATH", nested)
    log(_decision())  # Should not raise — creates parent dirs
    assert nested.exists()
