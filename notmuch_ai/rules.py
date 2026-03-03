"""
Rules engine: evaluates email against user-defined rules and built-in classifiers.

One job: given an email, return a list of tag operations.

Rules run cheapest-first:
  1. Static conditions (sender, subject pattern) — no LLM call
  2. LLM conditions — only if static conditions didn't match

Built-in rules (always evaluated, no config needed):
  - needs-reply: a real person wrote to you specifically and expects a response
  - ai-noise: auto-generated, newsletter — no human expects your response
  - ai-urgent: explicit deadline or C-level executive with blocking request
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


CONFIG_DIR = Path.home() / ".config" / "notmuch-ai"
RULES_FILE = CONFIG_DIR / "rules.yaml"


@dataclass
class TagOp:
    add: list[str] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)


@dataclass
class RuleMatch:
    rule_name: str
    rule_condition: str
    tags: TagOp
    llm_response: str | None = None
    reasoning: str = ""


@dataclass
class UserRule:
    name: str
    condition: str
    action_add: list[str]
    action_remove: list[str]
    # Optional static fast-path: skip LLM if these match
    static_from: list[str] = field(default_factory=list)
    static_subject: list[str] = field(default_factory=list)


def load_user_rules() -> list[UserRule]:
    """Load rules.yaml if it exists. Empty list if not."""
    if not RULES_FILE.exists():
        return []
    with open(RULES_FILE) as f:
        data = yaml.safe_load(f)
    if not data or "rules" not in data:
        return []
    rules = []
    for r in data["rules"]:
        action = r.get("action", "")
        add_tags: list[str] = []
        remove_tags: list[str] = []
        if isinstance(action, str):
            parts = action.split()
            if len(parts) >= 3 and parts[0] == "tag" and parts[1] == "add":
                add_tags = parts[2:]
            elif len(parts) >= 3 and parts[0] == "tag" and parts[1] == "remove":
                remove_tags = parts[2:]
        elif isinstance(action, dict):
            add_tags = action.get("add", [])
            remove_tags = action.get("remove", [])
        rules.append(
            UserRule(
                name=r["name"],
                condition=r["condition"],
                action_add=add_tags,
                action_remove=remove_tags,
                static_from=r.get("static_from", []),
                static_subject=r.get("static_subject", []),
            )
        )
    return rules


def _static_match(rule: UserRule, from_addr: str, subject: str) -> bool:
    """Check static conditions (no LLM). Returns True if any pattern matches."""
    for pattern in rule.static_from:
        if re.search(pattern, from_addr, re.IGNORECASE):
            return True
    for pattern in rule.static_subject:
        if re.search(pattern, subject, re.IGNORECASE):
            return True
    return False


def evaluate(
    from_addr: str,
    subject: str,
    body: str,
    tags: list[str],
    my_email: str = "",
    my_name: str = "",
    recipient_pos: str = "unknown",
    skip_llm: bool = False,
) -> list[RuleMatch]:
    """
    Evaluate an email against all rules (built-in + user-defined).

    Returns a list of matched rules with tag operations.
    Already-tagged messages skip the built-in classifiers for that tag.
    """
    matches: list[RuleMatch] = []

    # --- Built-in classifiers (single LLM call) ---
    if not skip_llm:
        matches += _builtin_classify(
            from_addr, subject, body, tags,
            my_email=my_email,
            my_name=my_name,
            recipient_pos=recipient_pos,
        )

    # --- User-defined rules ---
    for rule in load_user_rules():
        # Fast path: static match skips LLM
        if rule.static_from or rule.static_subject:
            if _static_match(rule, from_addr, subject):
                matches.append(
                    RuleMatch(
                        rule_name=rule.name,
                        rule_condition="static: " + rule.condition,
                        tags=TagOp(add=rule.action_add, remove=rule.action_remove),
                        reasoning="matched static pattern",
                    )
                )
                continue

        # LLM path
        if skip_llm:
            continue

        from notmuch_ai.llm import classify_condition

        result = classify_condition(rule.condition, subject, from_addr, body)
        if result.matches:
            matches.append(
                RuleMatch(
                    rule_name=rule.name,
                    rule_condition=rule.condition,
                    tags=TagOp(add=rule.action_add, remove=rule.action_remove),
                    llm_response=result.reasoning,
                    reasoning=result.reasoning,
                )
            )

    return matches


def _builtin_classify(
    from_addr: str,
    subject: str,
    body: str,
    existing_tags: list[str],
    my_email: str = "",
    my_name: str = "",
    recipient_pos: str = "unknown",
) -> list[RuleMatch]:
    """
    Run the three built-in classifiers in a single LLM call for efficiency.
    Skips tags already present on the message.
    """
    from notmuch_ai.llm import builtin_classify

    skip_needs_reply = "needs-reply" in existing_tags
    skip_noise = "ai-noise" in existing_tags
    skip_urgent = "ai-urgent" in existing_tags

    data = builtin_classify(
        from_addr=from_addr,
        subject=subject,
        body=body,
        my_email=my_email,
        my_name=my_name,
        recipient_pos=recipient_pos,
        skip_needs_reply=skip_needs_reply,
        skip_noise=skip_noise,
        skip_urgent=skip_urgent,
    )

    if not data:
        return []

    results: list[RuleMatch] = []

    if not skip_needs_reply and data.get("needs_reply"):
        results.append(
            RuleMatch(
                rule_name="built-in: needs-reply",
                rule_condition="a real person wrote this specifically to you expecting a personal response",
                tags=TagOp(add=["needs-reply"]),
                reasoning=data.get("needs_reply_reason", ""),
            )
        )

    if not skip_noise and data.get("is_noise"):
        results.append(
            RuleMatch(
                rule_name="built-in: ai-noise",
                rule_condition="auto-generated, newsletter, or notification — no human awaiting your response",
                tags=TagOp(add=["ai-noise"]),
                reasoning=data.get("is_noise_reason", ""),
            )
        )

    if not skip_urgent and data.get("is_urgent"):
        results.append(
            RuleMatch(
                rule_name="built-in: ai-urgent",
                rule_condition="explicit deadline within 24-48h or C-level/VP blocking request",
                tags=TagOp(add=["ai-urgent"]),
                reasoning=data.get("is_urgent_reason", ""),
            )
        )

    return results
