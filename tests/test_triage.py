"""Unit tests for triage.py — mocks DB, notmuch, LLM, and stdin."""

from __future__ import annotations

import io
import pytest

import notmuch_ai.db as db_module
import notmuch_ai.triage as triage_module
from notmuch_ai.triage import run_triage_session, _append_rule, _BUILTIN_TAGS, _get_all_tags
from notmuch_ai.notmuch import Email


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_email(**kwargs) -> Email:
    defaults = dict(
        message_id="test@example.com",
        subject="Test subject",
        from_addr="sender@example.com",
        to_addrs=["me@work.com"],
        cc_addrs=[],
        date="2026-03-04 10:00",
        body_text="Email body text.",
        tags=["inbox", "ai-noise"],
    )
    return Email(**{**defaults, **kwargs})


def _fake_decision(**kwargs) -> dict:
    defaults = dict(
        ts="2026-03-04T10:00:00+00:00",
        message_id="test@example.com",
        subject="Test subject",
        rule="built-in: ai-noise",
        tags_added=["ai-noise"],
        dry_run=False,
    )
    return {**defaults, **kwargs}


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "triage_test.db")


@pytest.fixture
def fake_rules_file(tmp_path, monkeypatch):
    rules_path = tmp_path / "rules.yaml"
    monkeypatch.setattr(triage_module, "RULES_FILE", rules_path)
    return rules_path


# ---------------------------------------------------------------------------
# run_triage_session: no decisions
# ---------------------------------------------------------------------------

def test_triage_empty_when_no_decisions(mocker):
    mocker.patch("notmuch_ai.triage.db.recent_untriaged", return_value=[])
    report = run_triage_session(limit=10)
    assert report.reviewed == 0
    assert report.confirmed == 0
    assert report.corrected == 0


# ---------------------------------------------------------------------------
# run_triage_session: confirm key
# ---------------------------------------------------------------------------

def test_triage_confirm_increments_confirmed(mocker, monkeypatch):
    mocker.patch("notmuch_ai.triage.db.recent_untriaged", return_value=[_fake_decision()])
    mocker.patch("notmuch_ai.triage.db.why", return_value=[{"llm_response": "noise email"}])
    mocker.patch("notmuch_ai.triage.nm.show", return_value=_fake_email())
    monkeypatch.setattr("sys.stdin", io.StringIO("c\n"))

    report = run_triage_session(limit=1)
    assert report.confirmed == 1
    assert report.corrected == 0


# ---------------------------------------------------------------------------
# run_triage_session: skip key
# ---------------------------------------------------------------------------

def test_triage_skip_increments_skipped(mocker, monkeypatch):
    mocker.patch("notmuch_ai.triage.db.recent_untriaged", return_value=[_fake_decision()])
    mocker.patch("notmuch_ai.triage.db.why", return_value=[])
    mocker.patch("notmuch_ai.triage.nm.show", return_value=_fake_email())
    monkeypatch.setattr("sys.stdin", io.StringIO("s\n"))

    report = run_triage_session(limit=1)
    assert report.skipped == 1
    assert report.confirmed == 0


# ---------------------------------------------------------------------------
# run_triage_session: quit key
# ---------------------------------------------------------------------------

def test_triage_quit_stops_processing(mocker, monkeypatch):
    decisions = [
        _fake_decision(message_id=f"msg{i}@x.com", subject=f"Email {i}")
        for i in range(5)
    ]
    mocker.patch("notmuch_ai.triage.db.recent_untriaged", return_value=decisions)
    mocker.patch("notmuch_ai.triage.db.why", return_value=[])
    mocker.patch("notmuch_ai.triage.nm.show", return_value=_fake_email())
    monkeypatch.setattr("sys.stdin", io.StringIO("q\n"))

    report = run_triage_session(limit=5)
    # q quits without reviewing any — reviewed count stays 0 before increment
    assert report.reviewed < 5


# ---------------------------------------------------------------------------
# run_triage_session: reclassify flow
# ---------------------------------------------------------------------------

def test_triage_reclassify_logs_correction(mocker, monkeypatch):
    mocker.patch("notmuch_ai.triage.db.recent_untriaged", return_value=[_fake_decision()])
    mocker.patch("notmuch_ai.triage.db.why", return_value=[])
    mocker.patch("notmuch_ai.triage.nm.show", return_value=_fake_email())
    mock_log = mocker.patch("notmuch_ai.triage.db.log_correction")
    mock_tag = mocker.patch("notmuch_ai.triage.nm.tag")
    # stub list_tags so _get_all_tags() returns a predictable list
    mocker.patch("notmuch_ai.triage.nm.list_tags", return_value=[])
    # r → reclassify, then choose option 4 (ai-fyi, 4th built-in)
    monkeypatch.setattr("sys.stdin", io.StringIO("r\n4\n"))

    report = run_triage_session(limit=1)
    assert report.corrected == 1
    mock_log.assert_called_once_with(
        "test@example.com",
        wrong_tag="ai-noise",
        correct_tag="ai-fyi",
    )
    mock_tag.assert_called_once_with("test@example.com", add=["ai-fyi"], remove=["ai-noise"])


def test_triage_reclassify_cancel_counts_as_skip(mocker, monkeypatch):
    mocker.patch("notmuch_ai.triage.db.recent_untriaged", return_value=[_fake_decision()])
    mocker.patch("notmuch_ai.triage.db.why", return_value=[])
    mocker.patch("notmuch_ai.triage.nm.show", return_value=_fake_email())
    mocker.patch("notmuch_ai.triage.db.log_correction")
    # r → reclassify, then cancel with 0
    monkeypatch.setattr("sys.stdin", io.StringIO("r\n0\n"))

    report = run_triage_session(limit=1)
    assert report.skipped == 1
    assert report.corrected == 0


# ---------------------------------------------------------------------------
# run_triage_session: unknown key loops until valid key received
# ---------------------------------------------------------------------------

def test_triage_unknown_key_loops_until_valid(mocker, monkeypatch):
    """Unknown key must not advance to next email — loop re-prompts until valid key."""
    mocker.patch("notmuch_ai.triage.db.recent_untriaged", return_value=[_fake_decision()])
    mocker.patch("notmuch_ai.triage.db.why", return_value=[])
    mocker.patch("notmuch_ai.triage.nm.show", return_value=_fake_email())
    # x is unknown, then c is confirm
    monkeypatch.setattr("sys.stdin", io.StringIO("x\nc\n"))

    report = run_triage_session(limit=1)
    assert report.confirmed == 1
    assert report.skipped == 0


# ---------------------------------------------------------------------------
# run_triage_session: deduplicate by message_id
# ---------------------------------------------------------------------------

def test_triage_deduplicates_message_ids(mocker, monkeypatch):
    """Same message_id appearing twice must only be shown once."""
    decisions = [
        _fake_decision(message_id="dup@x.com", rule="built-in: ai-noise"),
        _fake_decision(message_id="dup@x.com", rule="built-in: ai-urgent"),
    ]
    mocker.patch("notmuch_ai.triage.db.recent_untriaged", return_value=decisions)
    mocker.patch("notmuch_ai.triage.db.why", return_value=[])
    show_mock = mocker.patch("notmuch_ai.triage.nm.show", return_value=_fake_email())
    monkeypatch.setattr("sys.stdin", io.StringIO("s\n"))

    run_triage_session(limit=10)
    assert show_mock.call_count == 1


# ---------------------------------------------------------------------------
# run_triage_session: missing email is skipped gracefully
# ---------------------------------------------------------------------------

def test_triage_skips_missing_email(mocker, monkeypatch):
    mocker.patch("notmuch_ai.triage.db.recent_untriaged", return_value=[_fake_decision()])
    mocker.patch("notmuch_ai.triage.db.why", return_value=[])
    mocker.patch("notmuch_ai.triage.nm.show", return_value=None)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    report = run_triage_session(limit=1)
    # No email fetched → nothing reviewed
    assert report.reviewed == 0


# ---------------------------------------------------------------------------
# run_triage_session: rule proposals only when ≥2 corrections
# ---------------------------------------------------------------------------

def test_triage_no_rule_proposal_with_one_correction(mocker, monkeypatch):
    mocker.patch("notmuch_ai.triage.db.recent_untriaged", return_value=[_fake_decision()])
    mocker.patch("notmuch_ai.triage.db.why", return_value=[])
    mocker.patch("notmuch_ai.triage.nm.show", return_value=_fake_email())
    mocker.patch("notmuch_ai.triage.db.log_correction")
    suggest_mock = mocker.patch("notmuch_ai.llm.suggest_rules")
    monkeypatch.setattr("sys.stdin", io.StringIO("r\n4\n"))

    run_triage_session(limit=1)
    suggest_mock.assert_not_called()


def test_triage_proposes_rules_after_two_corrections(mocker, monkeypatch):
    decisions = [
        _fake_decision(message_id=f"m{i}@x.com", subject=f"Email {i}")
        for i in range(2)
    ]
    mocker.patch("notmuch_ai.triage.db.recent_untriaged", return_value=decisions)
    mocker.patch("notmuch_ai.triage.db.why", return_value=[])
    mocker.patch("notmuch_ai.triage.nm.show", return_value=_fake_email())
    mocker.patch("notmuch_ai.triage.db.log_correction")
    suggest_mock = mocker.patch("notmuch_ai.triage.suggest_rules", return_value=[])
    # Two corrections: r→4, r→4
    monkeypatch.setattr("sys.stdin", io.StringIO("r\n4\nr\n4\n"))

    run_triage_session(limit=2)
    suggest_mock.assert_called_once()


# ---------------------------------------------------------------------------
# _append_rule
# ---------------------------------------------------------------------------

def test_append_rule_creates_new_file(fake_rules_file, monkeypatch):
    monkeypatch.setattr(triage_module, "RULES_FILE", fake_rules_file)
    assert not fake_rules_file.exists()
    _append_rule({"name": "test-rule", "action": "tag add ai-fyi"})
    assert fake_rules_file.exists()
    import yaml
    data = yaml.safe_load(fake_rules_file.read_text())
    assert len(data["rules"]) == 1
    assert data["rules"][0]["name"] == "test-rule"


def test_append_rule_extends_existing_file(fake_rules_file, monkeypatch):
    monkeypatch.setattr(triage_module, "RULES_FILE", fake_rules_file)
    import yaml
    fake_rules_file.write_text(yaml.dump({"rules": [{"name": "existing", "action": "tag add ai-noise"}]}))
    _append_rule({"name": "new-rule", "action": "tag add ai-fyi"})
    data = yaml.safe_load(fake_rules_file.read_text())
    assert len(data["rules"]) == 2
    assert data["rules"][1]["name"] == "new-rule"


def test_append_rule_preserves_comments(fake_rules_file, monkeypatch):
    monkeypatch.setattr(triage_module, "RULES_FILE", fake_rules_file)
    original = (
        "# My carefully written header\n"
        "# with multiple comment lines\n"
        "rules:\n"
        "\n"
        "  # -- High-signal inbound --\n"
        "\n"
        "  - name: existing-rule\n"
        "    action: tag add ai-noise\n"
    )
    fake_rules_file.write_text(original)
    _append_rule({"name": "new-rule", "action": "tag add ai-fyi"})
    result = fake_rules_file.read_text()
    assert "# My carefully written header" in result
    assert "# with multiple comment lines" in result
    assert "# -- High-signal inbound --" in result
    assert "existing-rule" in result
    import yaml
    data = yaml.safe_load(result)
    assert len(data["rules"]) == 2


# ---------------------------------------------------------------------------
# _BUILTIN_TAGS completeness
# ---------------------------------------------------------------------------

def test_builtin_tags_contains_all_five():
    assert "needs-reply" in _BUILTIN_TAGS
    assert "ai-urgent" in _BUILTIN_TAGS
    assert "ai-noise" in _BUILTIN_TAGS
    assert "ai-fyi" in _BUILTIN_TAGS
    assert "ai-follow-up" in _BUILTIN_TAGS


def test_get_all_tags_includes_notmuch_tags(mocker):
    mocker.patch(
        "notmuch_ai.triage.nm.list_tags",
        return_value=["ai-newsletter", "github", "inbox", "unread", "work", "needs-reply"],
    )
    tags = _get_all_tags()
    assert "needs-reply" in tags
    assert "ai-newsletter" in tags
    assert "github" in tags
    assert "work" in tags
    assert "inbox" not in tags   # system tag filtered out
    assert "unread" not in tags  # system tag filtered out
    assert tags.index("needs-reply") < tags.index("ai-newsletter")  # builtins first


def test_get_all_tags_falls_back_gracefully_when_notmuch_unavailable(mocker):
    mocker.patch("notmuch_ai.triage.nm.list_tags", side_effect=Exception("notmuch not found"))
    tags = _get_all_tags()
    assert tags == _BUILTIN_TAGS  # falls back to built-ins only
