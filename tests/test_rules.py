"""Unit tests for rules.py — static matching + rules loading."""

from __future__ import annotations

import pytest
from pathlib import Path

import notmuch_ai.rules as rules_module
from notmuch_ai.rules import (
    UserRule, TagOp, load_user_rules, _static_match, evaluate,
)


# ---------------------------------------------------------------------------
# _static_match
# ---------------------------------------------------------------------------

def test_static_match_from_pattern():
    rule = UserRule(
        name="test", condition="", action_add=[], action_remove=[],
        static_from=[r"sales@.*\.com"],
    )
    assert _static_match(rule, "sales@company.com", "Hello") is True
    assert _static_match(rule, "boss@company.com", "Hello") is False


def test_static_match_subject_pattern():
    rule = UserRule(
        name="test", condition="", action_add=[], action_remove=[],
        static_subject=[r"(?i)quick question"],
    )
    assert _static_match(rule, "anyone@anywhere.com", "Quick Question about your service") is True
    assert _static_match(rule, "anyone@anywhere.com", "Normal email subject") is False


def test_static_match_case_insensitive():
    rule = UserRule(
        name="test", condition="", action_add=[], action_remove=[],
        static_subject=[r"(?i)partnership"],
    )
    assert _static_match(rule, "x@y.com", "PARTNERSHIP OPPORTUNITY") is True


def test_static_match_no_patterns():
    rule = UserRule(name="test", condition="", action_add=[], action_remove=[])
    assert _static_match(rule, "x@y.com", "anything") is False


# ---------------------------------------------------------------------------
# load_user_rules
# ---------------------------------------------------------------------------

SAMPLE_RULES_YAML = """
rules:
  - name: "Cold outreach"
    condition: "Is this a sales email?"
    action: tag add ai-cold-outreach
    static_subject:
      - "(?i)quick question"

  - name: "Remove noise"
    condition: "Is this noise?"
    action: tag remove inbox
"""


def test_load_user_rules_parses_yaml(tmp_path, monkeypatch):
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text(SAMPLE_RULES_YAML)
    monkeypatch.setattr(rules_module, "RULES_FILE", rules_file)

    rules = load_user_rules()
    assert len(rules) == 2
    assert rules[0].name == "Cold outreach"
    assert rules[0].action_add == ["ai-cold-outreach"]
    assert rules[0].action_remove == []
    assert rules[0].static_subject == ["(?i)quick question"]

    assert rules[1].action_add == []
    assert rules[1].action_remove == ["inbox"]


def test_load_user_rules_empty_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(rules_module, "RULES_FILE", tmp_path / "nonexistent.yaml")
    assert load_user_rules() == []


def test_load_user_rules_empty_when_no_rules_key(tmp_path, monkeypatch):
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text("other_key: value\n")
    monkeypatch.setattr(rules_module, "RULES_FILE", rules_file)
    assert load_user_rules() == []


# ---------------------------------------------------------------------------
# evaluate — static path (no LLM)
# ---------------------------------------------------------------------------

def test_evaluate_static_match_skips_llm(monkeypatch, mocker):
    """Static match fires without touching LLM."""
    rules_file_content = """
rules:
  - name: "Sales"
    condition: "sales email?"
    action: tag add ai-cold-outreach
    static_subject:
      - "(?i)partnership"
"""
    import tempfile
    tmp = Path(tempfile.mktemp(suffix=".yaml"))
    tmp.write_text(rules_file_content)
    monkeypatch.setattr(rules_module, "RULES_FILE", tmp)

    mock_llm = mocker.patch("notmuch_ai.llm.classify_condition")
    mocker.patch("notmuch_ai.rules._builtin_classify", return_value=[])

    matches = evaluate(
        from_addr="sales@company.com",
        subject="Partnership opportunity for you",
        body="Hi, I wanted to reach out...",
        tags=[],
        skip_llm=False,
    )

    # Static match fired — LLM should not have been called for the user rule
    mock_llm.assert_not_called()
    assert any(m.rule_name == "Sales" for m in matches)
    tmp.unlink()


def test_evaluate_skip_llm():
    """With skip_llm=True, no LLM calls happen and built-ins are skipped."""
    import notmuch_ai.rules as r
    original = r.RULES_FILE
    # Point to nonexistent rules file
    r.RULES_FILE = Path("/nonexistent/rules.yaml")
    try:
        matches = evaluate(
            from_addr="x@y.com", subject="s", body="b", tags=[],
            skip_llm=True,
        )
        assert matches == []
    finally:
        r.RULES_FILE = original


def test_evaluate_builtin_noise(mocker, monkeypatch):
    """Built-in classifier fires for noise emails."""
    import notmuch_ai.rules as r
    monkeypatch.setattr(r, "RULES_FILE", Path("/nonexistent.yaml"))

    mock_builtin = mocker.patch("notmuch_ai.rules._builtin_classify")
    from notmuch_ai.rules import RuleMatch, TagOp
    mock_builtin.return_value = [
        RuleMatch(
            rule_name="built-in: ai-noise",
            rule_condition="auto-generated",
            tags=TagOp(add=["ai-noise"]),
            reasoning="newsletter",
        )
    ]

    matches = evaluate(
        from_addr="news@substack.com",
        subject="Weekly digest",
        body="Top stories...",
        tags=[],
    )
    assert len(matches) == 1
    assert "ai-noise" in matches[0].tags.add


# ---------------------------------------------------------------------------
# static-only rules (no condition key)
# ---------------------------------------------------------------------------

STATIC_ONLY_YAML = """
rules:
  - name: "GitHub noise"
    action: tag add ai-notification
    static_from:
      - "notifications@github.com"
      - "noreply@github.com"
"""


def test_static_only_rule_loads(tmp_path, monkeypatch):
    """Rule with no condition key loads with condition=''."""
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text(STATIC_ONLY_YAML)
    monkeypatch.setattr(rules_module, "RULES_FILE", rules_file)
    rules = load_user_rules()
    assert len(rules) == 1
    assert rules[0].name == "GitHub noise"
    assert rules[0].condition == ""
    assert rules[0].action_add == ["ai-notification"]
    assert rules[0].static_from == ["notifications@github.com", "noreply@github.com"]


def test_static_only_rule_matches(tmp_path, monkeypatch, mocker):
    """Static-only rule matches on from_addr, LLM never called."""
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text(STATIC_ONLY_YAML)
    monkeypatch.setattr(rules_module, "RULES_FILE", rules_file)
    mocker.patch("notmuch_ai.rules._builtin_classify", return_value=[])
    mock_llm = mocker.patch("notmuch_ai.llm.classify_condition")

    matches = evaluate(
        from_addr="notifications@github.com",
        subject="[repo] New PR #42",
        body="Review requested...",
        tags=[],
    )
    mock_llm.assert_not_called()
    assert len(matches) == 1
    assert matches[0].rule_name == "GitHub noise"
    assert matches[0].tags.add == ["ai-notification"]


def test_static_only_rule_no_match_skips_llm(tmp_path, monkeypatch, mocker):
    """Static-only rule that doesn't match skips LLM (no condition to send)."""
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text(STATIC_ONLY_YAML)
    monkeypatch.setattr(rules_module, "RULES_FILE", rules_file)
    mocker.patch("notmuch_ai.rules._builtin_classify", return_value=[])
    mock_llm = mocker.patch("notmuch_ai.llm.classify_condition")

    matches = evaluate(
        from_addr="boss@company.com",
        subject="Meeting tomorrow",
        body="Let's sync up",
        tags=[],
    )
    mock_llm.assert_not_called()
    assert matches == []


def test_bad_regex_in_static_pattern():
    """Malformed regex in static patterns skips that pattern instead of crashing."""
    rule = UserRule(
        name="bad-regex", action_add=["test"],
        static_from=[r"[invalid(regex"],
        static_subject=[r"(?P<broken"],
    )
    assert _static_match(rule, "anything@example.com", "any subject") is False
