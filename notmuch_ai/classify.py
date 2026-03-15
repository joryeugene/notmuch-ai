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
    paused: bool = False
    static_only: bool = False


def classify_messages(
    query: str = "tag:inbox AND NOT tag:ai-classified",
    limit: int | None = None,
    dry_run: bool = False,
    verbose: bool = False,
    workers: int = 1,
) -> ClassifyReport:
    """
    Classify all messages matching query.

    After classification, applies tag:ai-classified so messages aren't re-processed.
    Reads user identity once from notmuch config and forwards to the rules engine.
    When workers > 1, LLM calls run in parallel threads while tag writes stay sequential.
    """
    from notmuch_ai.rules import CONFIG_DIR
    pause_flag = CONFIG_DIR / ".paused"
    if pause_flag.exists():
        return ClassifyReport(processed=0, tagged=0, skipped=0, errors=0, paused=True)

    from notmuch_ai.llm import llm_available

    my_email = notmuch.get_user_email()
    my_name = notmuch.get_user_name()
    has_llm = llm_available()
    skip_llm = not has_llm

    message_ids = notmuch.search(query, limit=limit)

    if workers > 1 and not skip_llm:
        return _classify_parallel(
            message_ids, workers=workers,
            my_email=my_email, my_name=my_name,
            dry_run=dry_run, verbose=verbose, skip_llm=skip_llm,
        )

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
                skip_llm=skip_llm,
            )
            if result:
                tagged += 1
            else:
                skipped += 1
        except Exception as e:
            errors += 1
            if verbose:
                print(f"  ERROR {mid}: {e}")

    return ClassifyReport(
        processed=processed, tagged=tagged, skipped=skipped, errors=errors,
        static_only=skip_llm,
    )


def count_unclassified(query: str = "tag:inbox AND NOT tag:ai-classified") -> int:
    """Return the number of inbox messages not yet classified."""
    return len(notmuch.search(query))


def count_pending_new(query: str = "tag:new AND NOT tag:ai-classified") -> int:
    """Return the number of new messages waiting to be classified."""
    return len(notmuch.search(query))


@dataclass
class _EvalResult:
    message_id: str
    email: notmuch.Email
    matches: list[rules.RuleMatch]


def _evaluate_one(
    message_id: str,
    my_email: str,
    my_name: str,
    skip_llm: bool = False,
) -> _EvalResult | None:
    """Evaluate phase: read email + run rules. Thread-safe (no writes)."""
    email = notmuch.show(message_id)
    if not email:
        return None
    pos = notmuch.recipient_position(email, my_email) if my_email else "unknown"
    matches = rules.evaluate(
        from_addr=email.from_addr,
        subject=email.subject,
        body=email.body_text,
        tags=email.tags,
        my_email=my_email,
        my_name=my_name,
        recipient_pos=pos,
        skip_llm=skip_llm,
    )
    return _EvalResult(message_id=message_id, email=email, matches=matches)


def _apply_tags(
    result: _EvalResult,
    dry_run: bool,
    verbose: bool,
) -> bool:
    """Apply phase: write tags + log. Must run in main thread (Xapian single-writer)."""
    email = result.email

    if verbose:
        print(f"  Classifying: {email.subject[:60]!r} from {email.from_addr}")

    if not dry_run:
        notmuch.tag(result.message_id, add=["ai-classified"])

    if not result.matches:
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

    for match in result.matches:
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


def _classify_parallel(
    message_ids: list[str],
    workers: int,
    my_email: str,
    my_name: str,
    dry_run: bool,
    verbose: bool,
    skip_llm: bool,
) -> ClassifyReport:
    """Parallel classification: LLM calls in threads, tag writes sequential."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    processed = 0
    tagged = 0
    skipped = 0
    errors = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_evaluate_one, mid, my_email, my_name, skip_llm): mid
            for mid in message_ids
        }
        for future in as_completed(futures):
            processed += 1
            mid = futures[future]
            try:
                result = future.result()
                if result is None:
                    skipped += 1
                    continue
                if _apply_tags(result, dry_run=dry_run, verbose=verbose):
                    tagged += 1
                else:
                    skipped += 1
            except Exception as e:
                errors += 1
                if verbose:
                    print(f"  ERROR {mid}: {e}")

    return ClassifyReport(
        processed=processed, tagged=tagged, skipped=skipped, errors=errors,
        static_only=skip_llm,
    )


def _classify_one(
    message_id: str,
    my_email: str,
    my_name: str,
    dry_run: bool,
    verbose: bool,
    skip_llm: bool = False,
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
        skip_llm=skip_llm,
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
