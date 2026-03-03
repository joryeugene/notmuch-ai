"""Unit tests for llm.py — mocks Anthropic client, tests JSON parsing."""

from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, patch

from notmuch_ai.llm import (
    classify_condition, builtin_classify, _parse_classify_result,
    ClassifyResult, DEFAULT_MODEL, DEFAULT_DRAFT_MODEL,
)


# ---------------------------------------------------------------------------
# _parse_classify_result (JSON parsing + fail-safe)
# ---------------------------------------------------------------------------

def test_parse_classify_result_matches():
    raw = json.dumps({"matches": True, "confidence": "high", "reasoning": "clearly a sales pitch"})
    result = _parse_classify_result(raw)
    assert result.matches is True
    assert result.confidence == "high"
    assert result.reasoning == "clearly a sales pitch"


def test_parse_classify_result_no_match():
    raw = json.dumps({"matches": False, "confidence": "low", "reasoning": "normal email"})
    result = _parse_classify_result(raw)
    assert result.matches is False


def test_parse_classify_result_strips_markdown_fence():
    raw = "```json\n{\"matches\": true, \"confidence\": \"medium\", \"reasoning\": \"ok\"}\n```"
    result = _parse_classify_result(raw)
    assert result.matches is True


def test_parse_classify_result_bad_json_returns_false():
    """Bad LLM output must never cause tags to be applied."""
    result = _parse_classify_result("This is not JSON at all.")
    assert result.matches is False
    assert result.confidence == "low"
    assert "Failed to parse" in result.reasoning


def test_parse_classify_result_empty_string():
    result = _parse_classify_result("")
    assert result.matches is False


# ---------------------------------------------------------------------------
# DEFAULT_MODEL is haiku (cheap)
# ---------------------------------------------------------------------------

def test_default_model_is_haiku():
    assert "haiku" in DEFAULT_MODEL.lower()


def test_default_draft_model_is_sonnet():
    assert "sonnet" in DEFAULT_DRAFT_MODEL.lower()


# ---------------------------------------------------------------------------
# classify_condition
# ---------------------------------------------------------------------------

def test_classify_condition_calls_anthropic(mocker):
    mock_call = mocker.patch("notmuch_ai.llm._call_anthropic")
    mock_call.return_value = json.dumps({
        "matches": True, "confidence": "high", "reasoning": "sales email"
    })
    result = classify_condition(
        condition="Is this a sales email?",
        email_subject="Quick question",
        email_from="sales@company.com",
        email_body="Hi, I wanted to reach out...",
    )
    assert result.matches is True
    assert mock_call.called


def test_classify_condition_fail_safe_on_api_error(mocker):
    mock_call = mocker.patch("notmuch_ai.llm._call_anthropic")
    mock_call.side_effect = Exception("API timeout")
    with pytest.raises(Exception):
        classify_condition("condition", "subject", "from@x.com", "body")


# ---------------------------------------------------------------------------
# builtin_classify
# ---------------------------------------------------------------------------

def _mock_builtin_response(needs_reply=False, is_noise=True, is_urgent=False):
    return json.dumps({
        "needs_reply": needs_reply,
        "needs_reply_reason": "test reason",
        "is_noise": is_noise,
        "is_noise_reason": "auto-generated",
        "is_urgent": is_urgent,
        "is_urgent_reason": "no deadline",
    })


def test_builtin_classify_noise_email(mocker):
    mock_call = mocker.patch("notmuch_ai.llm._call_anthropic")
    mock_call.return_value = _mock_builtin_response(is_noise=True)
    result = builtin_classify(
        from_addr="newsletter@substack.com",
        subject="Weekly digest",
        body="Top stories this week...",
        my_email="me@work.com",
        my_name="Me",
        recipient_pos="To",
    )
    assert result.get("is_noise") is True
    assert result.get("needs_reply") is False


def test_builtin_classify_needs_reply(mocker):
    mock_call = mocker.patch("notmuch_ai.llm._call_anthropic")
    mock_call.return_value = _mock_builtin_response(needs_reply=True, is_noise=False)
    result = builtin_classify(
        from_addr="boss@work.com",
        subject="Can you review this?",
        body="Hey, I need your feedback on...",
        my_email="me@work.com",
        my_name="Me",
        recipient_pos="To",
    )
    assert result.get("needs_reply") is True


def test_builtin_classify_returns_empty_on_bad_json(mocker):
    mock_call = mocker.patch("notmuch_ai.llm._call_anthropic")
    mock_call.return_value = "not json"
    result = builtin_classify(
        from_addr="x@y.com", subject="s", body="b",
        my_email="me@work.com", my_name="Me", recipient_pos="To",
    )
    assert result == {}


def test_builtin_classify_skips_when_all_already_tagged(mocker):
    mock_call = mocker.patch("notmuch_ai.llm._call_anthropic")
    result = builtin_classify(
        from_addr="x@y.com", subject="s", body="b",
        my_email="me@work.com", my_name="Me", recipient_pos="To",
        skip_needs_reply=True, skip_noise=True, skip_urgent=True,
    )
    assert result == {}
    mock_call.assert_not_called()


def test_builtin_classify_includes_recipient_context_in_prompt(mocker):
    mock_call = mocker.patch("notmuch_ai.llm._call_anthropic")
    mock_call.return_value = _mock_builtin_response()
    builtin_classify(
        from_addr="a@b.com", subject="s", body="b",
        my_email="me@work.com", my_name="Me",
        recipient_pos="Cc",
    )
    prompt = mock_call.call_args[0][0]
    assert "Cc" in prompt
    assert "me@work.com" in prompt
