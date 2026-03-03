"""
Classification pipeline.

One job: for each new message-id, run the rules engine and apply resulting tags.

Orchestrates: notmuch.py → rules.py → notmuch.py (tag) → db.py (log)
"""

from __future__ import annotations

from dataclasses import dataclass

from notmuch_ai import notmuch, db, rules


@dataclass
class ClassifyReport:
    processed: int
    tagged: int
    skipped: int
    errors: int


def classify_messages(
    query: str = "tag:inbox AND NOT tag:ai-classified",
    limit: int | None = None,
    dry_run: bool = False,
    verbose: bool = False,
) -> ClassifyReport:
    """
    Classify all messages matching query.

    After classification, applies tag:ai-classified so messages aren't re-processed.
    Reads user identity once from notmuch config and forwards to the rules engine.
    """
    my_email = notmuch.get_user_email()
    my_name = notmuch.get_user_name()

    message_ids = notmuch.search(query, limit=limit)
    processed = 0
    tagged = 0
    skipped = 0
    errors = 0

    for mid in message_ids:
        processed += 1
        try:
            result = _classify_one(
                mid,
                my_email=my_email,
                my_name=my_name,
                dry_run=dry_run,
                verbose=verbose,
            )
            if result:
                tagged += 1
            else:
                skipped += 1
        except Exception as e:
            errors += 1
            if verbose:
                print(f"  ERROR {mid}: {e}")

    return ClassifyReport(processed=processed, tagged=tagged, skipped=skipped, errors=errors)


def _classify_one(
    message_id: str,
    my_email: str,
    my_name: str,
    dry_run: bool,
    verbose: bool,
) -> bool:
    """Classify a single message. Returns True if any tags were applied."""
    email = notmuch.show(message_id)
    if not email:
        return False

    if verbose:
        print(f"  Classifying: {email.subject[:60]!r} from {email.from_addr}")

    pos = notmuch.recipient_position(email, my_email) if my_email else "unknown"

    matches = rules.evaluate(
        from_addr=email.from_addr,
        subject=email.subject,
        body=email.body_text,
        tags=email.tags,
        my_email=my_email,
        my_name=my_name,
        recipient_pos=pos,
    )

    # Mark as classified regardless of whether rules matched
    if not dry_run:
        notmuch.tag(message_id, add=["ai-classified"])

    if not matches:
        if verbose:
            print("    → no rules matched")
        db.log(db.Decision(
            message_id=email.message_id,
            subject=email.subject,
            from_addr=email.from_addr,
            rule_name="none",
            rule_condition="",
            tags_added=[],
            tags_removed=[],
            llm_response=None,
            dry_run=dry_run,
        ))
        return False

    all_added: list[str] = []
    all_removed: list[str] = []

    for match in matches:
        add_tags = [t for t in match.tags.add if t not in email.tags]
        remove_tags = [t for t in match.tags.remove if t in email.tags]

        if verbose:
            op_str = ""
            if add_tags:
                op_str += f"+{' +'.join(add_tags)} "
            if remove_tags:
                op_str += f"-{' -'.join(remove_tags)}"
            print(f"    → {match.rule_name}: {op_str.strip()} | {match.reasoning}")

        if not dry_run and (add_tags or remove_tags):
            notmuch.tag(email.message_id, add=add_tags, remove=remove_tags)

        all_added.extend(add_tags)
        all_removed.extend(remove_tags)

        db.log(db.Decision(
            message_id=email.message_id,
            subject=email.subject,
            from_addr=email.from_addr,
            rule_name=match.rule_name,
            rule_condition=match.rule_condition,
            tags_added=add_tags,
            tags_removed=remove_tags,
            llm_response=match.llm_response or match.reasoning or None,
            dry_run=dry_run,
        ))

    return bool(all_added or all_removed)
