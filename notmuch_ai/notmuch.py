"""
Thin stateless wrapper around the notmuch CLI.

Each function is a pure shell call — no caching, no state.
Input: python types. Output: python types or raises NotmuchError.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

MAX_BODY_DEPTH = 10  # Maximum recursion depth for multipart email body extraction


class NotmuchError(Exception):
    pass


def _run(args: list[str], input_text: str | None = None) -> str:
    result = subprocess.run(
        ["notmuch"] + args,
        capture_output=True,
        text=True,
        input=input_text,
        timeout=30,
    )
    if result.returncode != 0:
        raise NotmuchError(f"notmuch {args[0]} failed: {result.stderr.strip()}")
    return result.stdout


@dataclass
class Email:
    message_id: str
    subject: str
    from_addr: str
    to_addrs: list[str]
    cc_addrs: list[str]
    date: str
    body_text: str
    tags: list[str]


def search(query: str, limit: int | None = None) -> list[str]:
    """Return message-ids matching query.

    Note: notmuch ignores flags that appear after the search term, so --limit
    must be placed before the query string.
    """
    args = ["search", "--output=messages"]
    if limit:
        args.append(f"--limit={limit}")
    args.append(query)
    output = _run(args)
    return [line.strip() for line in output.splitlines() if line.strip()]


def show(message_id: str) -> Email | None:
    """Fetch a single email by message-id."""
    mid = message_id.lstrip("id:")
    output = _run(["show", "--format=json", "--body=true", f"id:{mid}"])
    data = json.loads(output)

    # notmuch show returns a nested list: [[thread, [message, ...]]]
    def _find_message(node: list) -> dict | None:
        for item in node:
            if isinstance(item, list):
                found = _find_message(item)
                if found:
                    return found
            elif isinstance(item, dict) and item.get("id") == mid:
                return item
        return None

    msg = _find_message(data)
    if not msg:
        return None

    headers = msg.get("headers", {})
    body_parts = msg.get("body", [])
    body_text = _extract_body_text(body_parts)

    # Parse To: and Cc: as lists (may contain multiple addresses)
    to_raw = headers.get("To", "")
    cc_raw = headers.get("Cc", "")

    return Email(
        message_id=msg["id"],
        subject=headers.get("Subject", ""),
        from_addr=headers.get("From", ""),
        to_addrs=_parse_addr_list(to_raw),
        cc_addrs=_parse_addr_list(cc_raw),
        date=headers.get("Date", ""),
        body_text=body_text,
        tags=msg.get("tags", []),
    )


def _parse_addr_list(header_value: str) -> list[str]:
    """Split a comma-separated address header into individual addresses."""
    if not header_value:
        return []
    return [addr.strip() for addr in header_value.split(",") if addr.strip()]


def _extract_body_text(parts: list, depth: int = 0) -> str:
    """Recursively extract plaintext body from notmuch body parts."""
    if depth > MAX_BODY_DEPTH:
        return ""
    texts: list[str] = []
    for part in parts:
        if isinstance(part, dict):
            content_type = part.get("content-type", "")
            if content_type == "text/plain":
                content = part.get("content", "")
                if isinstance(content, str):
                    texts.append(content)
            elif content_type.startswith("multipart/"):
                inner = part.get("content", [])
                if isinstance(inner, list):
                    texts.append(_extract_body_text(inner, depth + 1))
    return "\n".join(texts)


def tag(message_id: str, add: list[str] | None = None, remove: list[str] | None = None) -> None:
    """Apply tag changes to a single message."""
    changes: list[str] = []
    for t in add or []:
        changes.append(f"+{t}")
    for t in remove or []:
        changes.append(f"-{t}")
    if not changes:
        return
    mid = message_id.lstrip("id:")
    _run(["tag"] + changes + [f"id:{mid}"])


def new() -> int:
    """Run notmuch new and return count of new messages."""
    output = _run(["new"])
    # Output like: "Added 5 new messages to the database."
    for line in output.splitlines():
        if "Added" in line:
            parts = line.split()
            for i, word in enumerate(parts):
                if word == "Added" and i + 1 < len(parts):
                    try:
                        return int(parts[i + 1])
                    except ValueError:
                        pass
    return 0


def get_user_email() -> str:
    """Read the primary email address from notmuch config."""
    result = subprocess.run(
        ["notmuch", "config", "get", "user.primary_email"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return ""


def get_user_name() -> str:
    """Read the display name from notmuch config."""
    result = subprocess.run(
        ["notmuch", "config", "get", "user.name"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return ""


def recipient_position(email: Email, my_email: str) -> str:
    """
    Return 'To', 'Cc', or 'unknown' based on where my_email appears.
    Case-insensitive. Matches on email address substring.
    """
    my = my_email.lower()
    for addr in email.to_addrs:
        if my in addr.lower():
            return "To"
    for addr in email.cc_addrs:
        if my in addr.lower():
            return "Cc"
    return "unknown"
