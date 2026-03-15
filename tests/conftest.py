"""Shared test fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def mock_api_key(monkeypatch):
    """Ensure _provider() returns 'anthropic' in tests so existing mocks on _call_anthropic work."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-for-routing")
