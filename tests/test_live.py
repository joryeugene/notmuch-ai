"""
Live integration tests — run against real mail and real LLM.

Requires:
  - ANTHROPIC_API_KEY set
  - notmuch configured and indexed mail present

Run with:
  uv run pytest tests/test_live.py -v -s
  just test-live
"""

from __future__ import annotations

import os
import pytest
from pathlib import Path

import notmuch_ai.db as db_module
from notmuch_ai import notmuch
from notmuch_ai.classify import classify_messages
from notmuch_ai.rules import evaluate


# Skip all tests in this file if no API key
pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)

LIVE_LIMIT = 5  # Number of real inbox messages to classify


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Use a throwaway DB — live tests must not pollute the real audit.db."""
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "live_test.db")


def test_notmuch_is_available():
    """Verify notmuch is installed and responding."""
    ids = notmuch.search("tag:inbox", limit=1)
    assert isinstance(ids, list)


def test_get_user_identity():
    """Verify we can read user identity from notmuch config."""
    email = notmuch.get_user_email()
    assert "@" in email, f"Expected an email address, got: {email!r}"


def test_classify_inbox_dry_run(capsys):
    """
    Classify the first N inbox messages (dry run).
    Verify: no crashes, some decisions made, reasoning is non-empty.
    """
    report = classify_messages(
        query="tag:inbox",
        limit=LIVE_LIMIT,
        dry_run=True,
        verbose=True,
    )
    captured = capsys.readouterr()

    print(f"\n--- Live classification report ---")
    print(f"Processed: {report.processed}")
    print(f"Would tag: {report.tagged}")
    print(f"No match:  {report.skipped}")
    print(f"Errors:    {report.errors}")
    print(f"\n--- Verbose output ---")
    print(captured.out)

    assert report.processed > 0, "No messages found in inbox — is notmuch indexed?"
    assert report.errors == 0, f"Errors during classification: {report.errors}"


def test_classify_logs_decisions_to_db():
    """Verify decisions are written to audit DB during classify."""
    from notmuch_ai.db import recent

    classify_messages(
        query="tag:inbox",
        limit=LIVE_LIMIT,
        dry_run=True,
    )
    decisions = recent(limit=100)
    unique_messages = len({d["message_id"] for d in decisions})
    assert unique_messages == LIVE_LIMIT, (
        f"Expected {LIVE_LIMIT} unique messages logged, got {unique_messages} "
        f"({len(decisions)} total rows — multiple rules can fire on one message)"
    )


def test_show_real_email():
    """Fetch and parse a real email — verify structure."""
    ids = notmuch.search("tag:inbox", limit=1)
    assert ids, "No inbox messages found"

    email = notmuch.show(ids[0])
    assert email is not None
    assert email.message_id
    assert isinstance(email.tags, list)
    assert isinstance(email.to_addrs, list)


def test_recipient_position_real_email():
    """Verify recipient_position works with real email headers."""
    my_email = notmuch.get_user_email()
    ids = notmuch.search("tag:inbox", limit=3)
    for mid in ids:
        email = notmuch.show(mid)
        if email:
            pos = notmuch.recipient_position(email, my_email)
            assert pos in ("To", "Cc", "unknown"), f"Unexpected position: {pos}"
            break


def test_draft_generation():
    """Generate a draft reply for a real needs-reply email."""
    from notmuch_ai.draft import generate

    # Find an email that looks like it needs a reply
    ids = notmuch.search("tag:inbox", limit=10)
    assert ids, "No inbox messages"

    email = notmuch.show(ids[0])
    assert email

    draft = generate(ids[0])
    assert isinstance(draft, str)
    assert len(draft) > 10, "Draft should be non-trivial"
    print(f"\n--- Sample draft for: {email.subject!r} ---\n{draft}")


def test_builtin_classify_audit_output():
    """
    Classify inbox messages and print the full audit for manual review.
    This is the human-in-the-loop review step for prompt tuning.
    """
    from notmuch_ai.db import recent

    classify_messages(
        query="tag:inbox",
        limit=10,
        dry_run=True,
    )

    decisions = recent(limit=50)
    print(f"\n{'='*60}")
    print(f"AUDIT: {len(decisions)} decisions")
    print(f"{'='*60}")
    for d in decisions:
        tags = ", ".join(d["tags_added"]) or "(none)"
        reasoning = (d.get("llm_response") or "—")[:80]
        print(f"\n  Rule:      {d['rule']}")
        print(f"  Tags:      {tags}")
        print(f"  Reasoning: {reasoning}")

    # Spot-check: noise emails should not get needs-reply
    noise_also_needs_reply = [
        d for d in decisions
        if d["rule"] == "built-in: ai-noise" and
        any(other["rule"] == "built-in: needs-reply" and other["message_id"] == d["message_id"]
            for other in decisions)
    ]
    assert len(noise_also_needs_reply) == 0, (
        "Prompt violation: some emails tagged both ai-noise AND needs-reply "
        "(they are mutually exclusive)"
    )
