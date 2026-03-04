"""Unit tests for llm.py — mocks Anthropic client, tests JSON parsing."""

from __future__ import annotations

import json
import pytest

from notmuch_ai.llm import (
    classify_condition, builtin_classify, _parse_classify_result,
    suggest_rules, DEFAULT_MODEL, DEFAULT_DRAFT_MODEL,
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
        skip_fyi=True, skip_follow_up=True,
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


def test_builtin_classify_fyi_email(mocker):
    mock_call = mocker.patch("notmuch_ai.llm._call_anthropic")
    mock_call.return_value = json.dumps({
        "needs_reply": False, "needs_reply_reason": "no action needed",
        "is_noise": False, "is_noise_reason": "has genuine value",
        "is_urgent": False, "is_urgent_reason": "no deadline",
        "is_fyi": True, "is_fyi_reason": "company announcement, informational",
        "is_follow_up": False, "is_follow_up_reason": "can act immediately",
    })
    result = builtin_classify(
        from_addr="announcements@company.com",
        subject="Q1 all-hands recap",
        body="Here are the notes from today's all-hands...",
        my_email="me@work.com",
        my_name="Me",
        recipient_pos="To",
    )
    assert result.get("is_fyi") is True
    assert result.get("is_noise") is False


def test_builtin_classify_follow_up_email(mocker):
    mock_call = mocker.patch("notmuch_ai.llm._call_anthropic")
    mock_call.return_value = json.dumps({
        "needs_reply": False, "needs_reply_reason": "waiting on budget approval",
        "is_noise": False, "is_noise_reason": "real person",
        "is_urgent": False, "is_urgent_reason": "deadline next week",
        "is_fyi": False, "is_fyi_reason": "action required",
        "is_follow_up": True, "is_follow_up_reason": "needs budget sign-off first",
    })
    result = builtin_classify(
        from_addr="vendor@partner.com",
        subject="Contract renewal — please review when budget approved",
        body="Hi, as discussed, once budget is approved please send the signed contract.",
        my_email="me@work.com",
        my_name="Me",
        recipient_pos="To",
    )
    assert result.get("is_follow_up") is True


def test_builtin_classify_fyi_follow_up_prompt_contains_definitions(mocker):
    """Prompt must include ai-fyi and ai-follow-up definitions."""
    mock_call = mocker.patch("notmuch_ai.llm._call_anthropic")
    mock_call.return_value = _mock_builtin_response()
    builtin_classify(
        from_addr="a@b.com", subject="s", body="b",
        my_email="me@work.com", my_name="Me", recipient_pos="To",
    )
    prompt = mock_call.call_args[0][0]
    assert "is_fyi" in prompt
    assert "is_follow_up" in prompt


# ---------------------------------------------------------------------------
# suggest_rules
# ---------------------------------------------------------------------------

def test_suggest_rules_returns_list_on_valid_json(mocker):
    mock_call = mocker.patch("notmuch_ai.llm._call_anthropic")
    mock_call.return_value = json.dumps([
        {
            "name": "company-announcements",
            "static_from": ["@company\\.com"],
            "action": "tag add ai-fyi",
        }
    ])
    corrections = [
        {"message_id": "id1", "wrong_tag": "ai-noise", "correct_tag": "ai-fyi",
         "subject": "Q1 all-hands recap", "from_addr": "ceo@company.com"},
        {"message_id": "id2", "wrong_tag": "ai-noise", "correct_tag": "ai-fyi",
         "subject": "Org update", "from_addr": "hr@company.com"},
    ]
    result = suggest_rules(corrections)
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["name"] == "company-announcements"


def test_suggest_rules_empty_corrections_skips_llm(mocker):
    mock_call = mocker.patch("notmuch_ai.llm._call_anthropic")
    result = suggest_rules([])
    assert result == []
    mock_call.assert_not_called()


def test_suggest_rules_returns_empty_on_bad_json(mocker):
    mock_call = mocker.patch("notmuch_ai.llm._call_anthropic")
    mock_call.return_value = "not json at all"
    corrections = [
        {"message_id": "id1", "wrong_tag": "ai-noise", "correct_tag": "ai-fyi",
         "subject": "s", "from_addr": "x@y.com"},
    ]
    result = suggest_rules(corrections)
    assert result == []


def test_suggest_rules_returns_empty_when_llm_returns_object(mocker):
    """LLM accidentally returns a dict instead of list — fail safe."""
    mock_call = mocker.patch("notmuch_ai.llm._call_anthropic")
    mock_call.return_value = json.dumps({"name": "rule", "action": "tag add ai-fyi"})
    corrections = [
        {"message_id": "id1", "wrong_tag": "ai-noise", "correct_tag": "ai-fyi",
         "subject": "s", "from_addr": "x@y.com"},
    ]
    result = suggest_rules(corrections)
    assert result == []


def test_suggest_rules_prompt_contains_corrections(mocker):
    mock_call = mocker.patch("notmuch_ai.llm._call_anthropic")
    mock_call.return_value = "[]"
    corrections = [
        {"message_id": "id1", "wrong_tag": "ai-noise", "correct_tag": "ai-fyi",
         "subject": "Q1 recap", "from_addr": "hr@company.com"},
    ]
    suggest_rules(corrections)
    prompt = mock_call.call_args[0][0]
    assert "hr@company.com" in prompt
    assert "ai-noise" in prompt
    assert "ai-fyi" in prompt
