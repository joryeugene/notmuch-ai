"""
Reply draft generation.

One job: given a message-id, return a reply draft as plain text.
Caller decides what to do with it (pipe to aerc compose, open in nvim, etc.)
"""

from __future__ import annotations

import os

from notmuch_ai import notmuch, llm


def generate(message_id: str, context: str = "") -> str:
    """
    Fetch the message and generate a reply draft.

    Returns the draft body as a plain text string.
    Raises ValueError if the message isn't found.
    """
    email = notmuch.show(message_id)
    if not email:
        raise ValueError(f"Message not found: {message_id}")

    my_email = _my_email()

    return llm.generate_draft(
        original_from=email.from_addr,
        original_subject=email.subject,
        original_body=email.body_text,
        my_email=my_email,
        context=context,
    )


def _my_email() -> str:
    """Read the user's email from environment or notmuch config."""
    if addr := os.environ.get("NOTMUCH_AI_MY_EMAIL"):
        return addr

    # Try to read from notmuch config
    import subprocess

    result = subprocess.run(
        ["notmuch", "config", "get", "user.primary_email"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()

    return "me"
