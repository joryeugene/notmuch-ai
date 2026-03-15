"""Unit tests for cli.py helpers."""

from __future__ import annotations

import pytest
import yaml

import notmuch_ai.cli as cli_mod
import notmuch_ai.db as db_module


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """Redirect CONFIG_FILE to a temp path and return it."""
    config = tmp_path / "config.yaml"
    monkeypatch.setattr(cli_mod, "CONFIG_FILE", config)
    return config


def test_load_sync_command_no_file(tmp_config):
    assert cli_mod._load_sync_command() is None


def test_load_sync_command_with_command(tmp_config):
    tmp_config.write_text(yaml.dump({"sync_command": "mbsync -a"}))
    assert cli_mod._load_sync_command() == "mbsync -a"


def test_load_sync_command_empty_string(tmp_config):
    tmp_config.write_text(yaml.dump({"sync_command": ""}))
    assert cli_mod._load_sync_command() is None


def test_load_sync_command_whitespace_only(tmp_config):
    tmp_config.write_text(yaml.dump({"sync_command": "   "}))
    assert cli_mod._load_sync_command() is None


def test_load_sync_command_missing_key(tmp_config):
    tmp_config.write_text(yaml.dump({"other_key": "value"}))
    assert cli_mod._load_sync_command() is None


def test_load_sync_command_malformed_yaml(tmp_config):
    """Malformed YAML returns None without raising."""
    tmp_config.write_text(": bad: yaml: }{")
    assert cli_mod._load_sync_command() is None
