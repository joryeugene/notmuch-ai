"""
LLM abstraction layer.

One job: send a prompt, return a structured response.
Provider is configurable. Haiku default for classification (cheap + fast).
Sonnet default for drafts (better quality).

All callers pass plain text prompts and receive plain text back.
Structured extraction (JSON) happens here, not in callers.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass
class ClassifyResult:
    matches: bool
    confidence: str  # "high" | "medium" | "low"
    reasoning: str


# Haiku: fast + cheap for classification (~$0.0012/email at 100 emails/day = ~$3/month)
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
# Sonnet: better quality for draft generation
DEFAULT_DRAFT_MODEL = "claude-sonnet-4-6"


def _model() -> str:
    return os.environ.get("NOTMUCH_AI_MODEL", DEFAULT_MODEL)


def _draft_model() -> str:
    return os.environ.get("NOTMUCH_AI_DRAFT_MODEL", DEFAULT_DRAFT_MODEL)


def classify_condition(
    condition: str,
    email_subject: str,
    email_from: str,
    email_body: str,
) -> ClassifyResult:
    """
    Ask the LLM: does this email match the given natural-language condition?

    Returns a ClassifyResult with match verdict + reasoning.
    Used for user-defined rules.
    """
    prompt = f"""You are an email classifier. Answer whether the following email matches the given condition.

CONDITION: {condition}

EMAIL FROM: {email_from}
EMAIL SUBJECT: {email_subject}
EMAIL BODY (first 1500 chars):
{email_body[:1500]}

Respond with JSON only, no other text:
{{
  "matches": true or false,
  "confidence": "high" or "medium" or "low",
  "reasoning": "one sentence explaining why"
}}"""

    response_text = _call(prompt)
    return _parse_classify_result(response_text)


def _parse_classify_result(response_text: str) -> ClassifyResult:
    text = response_text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        data = json.loads(text)
        return ClassifyResult(
            matches=bool(data.get("matches", False)),
            confidence=str(data.get("confidence", "low")),
            reasoning=str(data.get("reasoning", "")),
        )
    except (json.JSONDecodeError, KeyError):
        # Fail safe: never apply tags on bad LLM output
        return ClassifyResult(
            matches=False,
            confidence="low",
            reasoning=f"Failed to parse LLM response: {response_text[:200]}",
        )


def builtin_classify(
    from_addr: str,
    subject: str,
    body: str,
    my_email: str,
    my_name: str,
    recipient_pos: str,
    skip_needs_reply: bool = False,
    skip_noise: bool = False,
    skip_urgent: bool = False,
) -> dict:
    """
    Run the three built-in classifiers in a single LLM call.

    Returns dict with keys: needs_reply, needs_reply_reason, is_noise,
    is_noise_reason, is_urgent, is_urgent_reason.
    Returns empty dict on parse failure (fail safe).
    """
    if skip_needs_reply and skip_noise and skip_urgent:
        return {}

    name_str = my_name or my_email or "the recipient"

    if recipient_pos == "To":
        recipient_note = f"{name_str} is in the **To:** field — this email was sent directly to them."
    elif recipient_pos == "Cc":
        recipient_note = f"{name_str} is in the **Cc:** field — they were copied, not the primary recipient. CC'd emails rarely need a personal reply."
    else:
        recipient_note = ""

    prompt = f"""You are classifying an email for {name_str} <{my_email}>.
{recipient_note}

EMAIL FROM: {from_addr}
SUBJECT: {subject}
BODY (first 1500 chars):
{body[:1500]}

Respond with JSON only:
{{
  "needs_reply": true or false,
  "needs_reply_reason": "one sentence",
  "is_noise": true or false,
  "is_noise_reason": "one sentence",
  "is_urgent": true or false,
  "is_urgent_reason": "one sentence"
}}

Definitions:
- needs_reply: A real human wrote this specifically to {name_str} expecting a personal response.
  CC'd emails almost never need a reply — they were just informed, not asked.
  Newsletters, system notifications, auto-generated mail: always false.
  needs_reply and is_noise CANNOT both be true.

- is_noise: Auto-generated, newsletter, system notification, or marketing — no human is waiting for {name_str}'s personal response.
  needs_reply and is_noise CANNOT both be true.

- is_urgent: Contains an explicit deadline within 24-48 hours, OR is from a C-level/VP-level executive with a blocking request.
  "Please review when you get a chance" is NOT urgent."""

    try:
        raw = _call(prompt).strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(raw)
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        # Fail safe: never apply tags on unparseable LLM output
        return {}


def generate_draft(
    original_from: str,
    original_subject: str,
    original_body: str,
    my_email: str,
    context: str = "",
) -> str:
    """
    Generate a reply draft. Returns plain text suitable for an email body.
    Uses the draft model (sonnet) for better quality.
    """
    context_block = f"\nAdditional context: {context}" if context else ""
    prompt = f"""You are helping draft a reply to an email. Write a professional, concise reply.

ORIGINAL EMAIL:
From: {original_from}
Subject: {original_subject}
Body:
{original_body[:2000]}

I am: {my_email}{context_block}

Write just the reply body text. No subject line, no "From:", no headers.
Be direct and professional. Match the tone of the original email."""

    return _call(prompt, model=_draft_model()).strip()


def _call(prompt: str, model: str | None = None) -> str:
    """
    Route to the configured provider via litellm.
    Falls back to anthropic SDK directly for claude models (more reliable).
    """
    m = model or _model()

    if m.startswith("claude"):
        return _call_anthropic(prompt, m)
    else:
        return _call_litellm(prompt, m)


def _call_anthropic(prompt: str, model: str) -> str:
    import anthropic

    client = anthropic.Anthropic()
    message = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def _call_litellm(prompt: str, model: str) -> str:
    import litellm

    response = litellm.completion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1024,
    )
    return response.choices[0].message.content or ""
