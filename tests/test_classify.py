"""Unit tests for classify.py — mocks notmuch + rules."""

from __future__ import annotations

import pytest

import notmuch_ai.db as db_module
from notmuch_ai.notmuch import Email
from notmuch_ai.classify import classify_messages
from notmuch_ai.rules import RuleMatch, TagOp


def _email(message_id="abc123", subject="Test", from_addr="sender@x.com", tags=None):
    return Email(
        message_id=message_id,
        subject=subject,
        from_addr=from_addr,
        to_addrs=["me@work.com"],
        cc_addrs=[],
        date="Mon, 1 Jan 2024",
        body_text="Hello",
        tags=tags or ["inbox"],
    )


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")


@pytest.fixture
def mock_notmuch(mocker):
    mocker.patch("notmuch_ai.classify.notmuch.get_user_email", return_value="me@work.com")
    mocker.patch("notmuch_ai.classify.notmuch.get_user_name", return_value="Me")
    mocker.patch("notmuch_ai.classify.notmuch.search", return_value=["abc123"])
    mocker.patch("notmuch_ai.classify.notmuch.show", return_value=_email())
    mocker.patch("notmuch_ai.classify.notmuch.tag")
    mocker.patch("notmuch_ai.classify.notmuch.recipient_position", return_value="To")
    return mocker


def test_classify_messages_dry_run_no_tags(mock_notmuch, mocker):
    """Dry run must not call notmuch.tag."""
    tag_mock = mocker.patch("notmuch_ai.classify.notmuch.tag")
    mocker.patch("notmuch_ai.classify.rules.evaluate", return_value=[])
    classify_messages(dry_run=True)
    tag_mock.assert_not_called()


def test_classify_messages_applies_matched_tags(mock_notmuch, mocker):
    tag_mock = mocker.patch("notmuch_ai.classify.notmuch.tag")
    mocker.patch(
        "notmuch_ai.classify.rules.evaluate",
        return_value=[
            RuleMatch(
                rule_name="built-in: needs-reply",
                rule_condition="direct ask",
                tags=TagOp(add=["needs-reply"]),
                reasoning="boss sent this",
            )
        ],
    )
    report = classify_messages(dry_run=False)
    assert report.tagged == 1
    assert report.processed == 1
    # Tag calls: once for needs-reply, once for ai-classified
    assert tag_mock.call_count == 2


def test_classify_messages_skipped_when_no_match(mock_notmuch, mocker):
    mocker.patch("notmuch_ai.classify.notmuch.tag")
    mocker.patch("notmuch_ai.classify.rules.evaluate", return_value=[])
    report = classify_messages()
    assert report.tagged == 0
    assert report.skipped == 1


def test_classify_messages_handles_error(mock_notmuch, mocker):
    mocker.patch("notmuch_ai.classify.notmuch.show", side_effect=Exception("notmuch exploded"))
    mocker.patch("notmuch_ai.classify.notmuch.tag")
    report = classify_messages()
    assert report.errors == 1
    assert report.processed == 1


def test_classify_messages_report_counts(mock_notmuch, mocker):
    mocker.patch("notmuch_ai.classify.notmuch.search", return_value=["id1", "id2", "id3"])
    mocker.patch("notmuch_ai.classify.notmuch.show", return_value=_email())
    mocker.patch("notmuch_ai.classify.notmuch.tag")
    mocker.patch(
        "notmuch_ai.classify.rules.evaluate",
        side_effect=[
            [RuleMatch("r1", "c", TagOp(add=["needs-reply"]), reasoning="x")],
            [],
            [],
        ]
    )
    report = classify_messages()
    assert report.processed == 3
    assert report.tagged == 1
    assert report.skipped == 2


def test_classify_skips_already_applied_tags(mock_notmuch, mocker):
    """Tags already on the email should not be re-applied."""
    email_with_tag = _email(tags=["inbox", "needs-reply"])
    mocker.patch("notmuch_ai.classify.notmuch.show", return_value=email_with_tag)
    tag_mock = mocker.patch("notmuch_ai.classify.notmuch.tag")
    mocker.patch(
        "notmuch_ai.classify.rules.evaluate",
        return_value=[
            RuleMatch("r", "c", TagOp(add=["needs-reply"]), reasoning="y")
        ],
    )
    classify_messages(dry_run=False)
    # needs-reply already present — only ai-classified tag call
    calls = [str(c) for c in tag_mock.call_args_list]
    assert not any("needs-reply" in c for c in calls)
