"""Unit tests for notmuch.py — all subprocess calls mocked."""

from __future__ import annotations

import json
import pytest
from unittest.mock import patch, MagicMock

from notmuch_ai.notmuch import (
    Email, NotmuchError, _extract_body_text, _parse_addr_list,
    search, show, tag, get_user_email, get_user_name, recipient_position,
)


# ---------------------------------------------------------------------------
# _parse_addr_list
# ---------------------------------------------------------------------------

def test_parse_addr_list_single():
    assert _parse_addr_list("alice@example.com") == ["alice@example.com"]


def test_parse_addr_list_multiple():
    result = _parse_addr_list("alice@example.com, Bob <bob@example.com>")
    assert result == ["alice@example.com", "Bob <bob@example.com>"]


def test_parse_addr_list_empty():
    assert _parse_addr_list("") == []


# ---------------------------------------------------------------------------
# _extract_body_text
# ---------------------------------------------------------------------------

def test_extract_body_text_plain():
    parts = [{"content-type": "text/plain", "content": "Hello world"}]
    assert _extract_body_text(parts) == "Hello world"


def test_extract_body_text_multipart():
    parts = [{
        "content-type": "multipart/alternative",
        "content": [
            {"content-type": "text/plain", "content": "Plain text"},
            {"content-type": "text/html", "content": "<p>HTML</p>"},
        ]
    }]
    assert "Plain text" in _extract_body_text(parts)


def test_extract_body_text_empty():
    assert _extract_body_text([]) == ""


def test_extract_body_text_depth_limit():
    # Deep nesting should not crash — terminates at depth 10
    deep = {"content-type": "multipart/mixed", "content": []}
    node = deep
    for _ in range(15):
        child = {"content-type": "multipart/mixed", "content": []}
        node["content"].append(child)
        node = child
    node["content"].append({"content-type": "text/plain", "content": "deep"})
    assert _extract_body_text([deep]) == ""  # depth limit cuts off


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def test_search_returns_message_ids(mocker):
    mock_run = mocker.patch("notmuch_ai.notmuch._run")
    mock_run.return_value = "id:abc@mail.com\nid:def@mail.com\n"
    result = search("tag:inbox")
    assert result == ["id:abc@mail.com", "id:def@mail.com"]
    mock_run.assert_called_once_with(["search", "--output=messages", "tag:inbox"])


def test_search_with_limit(mocker):
    mock_run = mocker.patch("notmuch_ai.notmuch._run")
    mock_run.return_value = "id:abc@mail.com\n"
    search("tag:inbox", limit=5)
    # --limit must come before the query or notmuch ignores it
    mock_run.assert_called_once_with(
        ["search", "--output=messages", "--limit=5", "tag:inbox"]
    )


def test_search_empty_result(mocker):
    mock_run = mocker.patch("notmuch_ai.notmuch._run")
    mock_run.return_value = ""
    assert search("tag:nothing") == []


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------

def _make_notmuch_show_response(message_id: str, subject: str = "Test", from_addr: str = "sender@example.com", to: str = "me@example.com", body: str = "Hello") -> str:
    msg = {
        "id": message_id,
        "headers": {
            "Subject": subject,
            "From": from_addr,
            "To": to,
            "Cc": "",
            "Date": "Mon, 1 Jan 2024 12:00:00 +0000",
        },
        "tags": ["inbox", "unread"],
        "body": [{"content-type": "text/plain", "content": body}],
    }
    return json.dumps([[{}, [msg, []]]])


def test_show_returns_email(mocker):
    mock_run = mocker.patch("notmuch_ai.notmuch._run")
    mock_run.return_value = _make_notmuch_show_response("abc123")
    email = show("abc123")
    assert email is not None
    assert email.message_id == "abc123"
    assert email.subject == "Test"
    assert email.from_addr == "sender@example.com"
    assert "me@example.com" in email.to_addrs
    assert email.body_text == "Hello"
    assert "inbox" in email.tags


def test_show_strips_id_prefix(mocker):
    mock_run = mocker.patch("notmuch_ai.notmuch._run")
    mock_run.return_value = _make_notmuch_show_response("abc123")
    email = show("id:abc123")
    assert email is not None
    assert email.message_id == "abc123"
    mock_run.assert_called_once_with(
        ["show", "--format=json", "--body=true", "id:abc123"]
    )


def test_show_returns_none_when_not_found(mocker):
    mock_run = mocker.patch("notmuch_ai.notmuch._run")
    mock_run.return_value = json.dumps([[{}, []]])
    assert show("nonexistent") is None


# ---------------------------------------------------------------------------
# tag
# ---------------------------------------------------------------------------

def test_tag_add(mocker):
    mock_run = mocker.patch("notmuch_ai.notmuch._run")
    tag("abc123", add=["needs-reply"])
    mock_run.assert_called_once_with(["tag", "+needs-reply", "id:abc123"])


def test_tag_remove(mocker):
    mock_run = mocker.patch("notmuch_ai.notmuch._run")
    tag("abc123", remove=["inbox"])
    mock_run.assert_called_once_with(["tag", "-inbox", "id:abc123"])


def test_tag_add_and_remove(mocker):
    mock_run = mocker.patch("notmuch_ai.notmuch._run")
    tag("abc123", add=["needs-reply"], remove=["unread"])
    mock_run.assert_called_once_with(["tag", "+needs-reply", "-unread", "id:abc123"])


def test_tag_noop_when_empty(mocker):
    mock_run = mocker.patch("notmuch_ai.notmuch._run")
    tag("abc123")
    mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# get_user_email / get_user_name
# ---------------------------------------------------------------------------

def test_get_user_email_success(mocker):
    mock_run = mocker.patch("subprocess.run")
    mock_run.return_value = MagicMock(returncode=0, stdout="user@example.com\n")
    assert get_user_email() == "user@example.com"


def test_get_user_email_failure(mocker):
    mock_run = mocker.patch("subprocess.run")
    mock_run.return_value = MagicMock(returncode=1, stdout="")
    assert get_user_email() == ""


# ---------------------------------------------------------------------------
# recipient_position
# ---------------------------------------------------------------------------

def test_recipient_position_to():
    email = Email(
        message_id="x", subject="s", from_addr="a@b.com",
        to_addrs=["User <user@example.com>"],
        cc_addrs=[],
        date="", body_text="", tags=[],
    )
    assert recipient_position(email, "user@example.com") == "To"


def test_recipient_position_cc():
    email = Email(
        message_id="x", subject="s", from_addr="a@b.com",
        to_addrs=["boss@example.com"],
        cc_addrs=["User <user@example.com>"],
        date="", body_text="", tags=[],
    )
    assert recipient_position(email, "user@example.com") == "Cc"


def test_recipient_position_unknown():
    email = Email(
        message_id="x", subject="s", from_addr="a@b.com",
        to_addrs=["other@example.com"],
        cc_addrs=[],
        date="", body_text="", tags=[],
    )
    assert recipient_position(email, "user@example.com") == "unknown"
