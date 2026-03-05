"""
Interactive triage session for reviewing AI classification decisions.

One job: show recent classifications one at a time, collect user corrections,
propose new YAML rules from patterns in those corrections.

Key bindings: [c] confirm  [r] reclassify  [s] skip  [q] quit
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from notmuch_ai import db, notmuch as nm
from notmuch_ai.llm import suggest_rules
from notmuch_ai.rules import RULES_FILE


_BUILTIN_TAGS = ["needs-reply", "ai-urgent", "ai-noise", "ai-fyi", "ai-follow-up"]

# notmuch system/status tags — never meaningful as classification targets
_SYSTEM_TAGS = frozenset({
    "inbox", "unread", "replied", "deleted", "sent",
    "attachment", "flagged", "draft", "passed", "ai-classified",
})


def _get_all_tags() -> list[str]:
    """Return built-in tags + all tags in the notmuch DB, excluding system tags."""
    try:
        notmuch_tags = nm.list_tags()
    except Exception:
        notmuch_tags = []
    custom = sorted(
        t for t in notmuch_tags
        if t not in _BUILTIN_TAGS and t not in _SYSTEM_TAGS
    )
    return _BUILTIN_TAGS + custom

console = Console()


@dataclass
class TriageReport:
    reviewed: int = 0
    confirmed: int = 0
    corrected: int = 0
    skipped: int = 0
    rules_added: int = 0


@dataclass
class _Correction:
    message_id: str
    wrong_tag: str
    correct_tag: str
    subject: str
    from_addr: str


def run_triage_session(limit: int = 20) -> TriageReport:
    """
    Interactive review of recent AI classifications.
    Returns a TriageReport with correction and rule-proposal counts.
    """
    report = TriageReport()
    corrections: list[_Correction] = []

    # Deduplicate by message_id — one triage entry per email
    decisions = db.recent_untriaged(limit=limit)
    seen: set[str] = set()
    unique: list[dict] = []
    for d in decisions:
        mid = d["message_id"]
        if mid not in seen:
            seen.add(mid)
            unique.append(d)

    if not unique:
        console.print("[yellow]No recent classifications to review.[/yellow]")
        console.print("Run [cyan]notmuch-ai classify[/cyan] first.")
        return report

    total = len(unique)
    console.print(f"\n[bold]Triage:[/bold] reviewing [cyan]{total}[/cyan] recent classifications\n")

    for idx, decision in enumerate(unique, 1):
        mid = decision["message_id"]

        # Fetch full email — skip if not found (deleted/moved)
        email = nm.show(mid)
        if email is None:
            db.log_triage_review(mid, action="skipped")
            continue

        # Fetch most recent decision detail for reasoning
        history = db.why(mid)
        reasoning = history[0].get("llm_response") or "" if history else ""

        current_tags = decision.get("tags_added") or []
        current_tag = current_tags[0] if current_tags else decision.get("rule", "?")

        _render_panel(email, current_tag, reasoning, idx, total)

        while True:
            key = _getchar_prompt()
            if key in ("c", "r", "s", "q"):
                break
            console.print("[dim]  unknown key[/dim]\n")

        if key == "q":
            console.print("\n[dim]Quitting triage.[/dim]")
            break
        elif key == "s":
            db.log_triage_review(mid, action="skipped")
            report.skipped += 1
            console.print("[dim]  → skipped[/dim]\n")
        elif key == "c":
            db.log_triage_review(mid, action="confirmed")
            report.confirmed += 1
            console.print("[green]  → confirmed[/green]\n")
        elif key == "r":
            correct_tag = _prompt_reclassify(current_tag)
            if correct_tag and correct_tag != current_tag:
                db.log_correction(mid, wrong_tag=current_tag, correct_tag=correct_tag)
                nm.tag(mid, add=[correct_tag], remove=[current_tag])
                corrections.append(_Correction(
                    message_id=mid,
                    wrong_tag=current_tag,
                    correct_tag=correct_tag,
                    subject=email.subject or "",
                    from_addr=email.from_addr or "",
                ))
                db.log_triage_review(mid, action="corrected")
                report.corrected += 1
                console.print(f"[cyan]  → corrected:[/cyan] {current_tag} → {correct_tag}\n")
            else:
                db.log_triage_review(mid, action="skipped")
                report.skipped += 1
                console.print("[dim]  → unchanged, skipped[/dim]\n")

        report.reviewed += 1

    # Rule proposal phase
    if len(corrections) >= 2:
        report.rules_added = _propose_rules(corrections)
    elif corrections:
        console.print("[dim]1 correction — need ≥2 to propose rules.[/dim]\n")

    _print_summary(report)
    return report


def _render_panel(email: nm.Email, current_tag: str, reasoning: str, idx: int, total: int) -> None:
    tag_color = {
        "needs-reply": "green",
        "ai-urgent": "red",
        "ai-noise": "dim",
        "ai-fyi": "blue",
        "ai-follow-up": "yellow",
    }.get(current_tag, "white")

    body = Text()
    body.append(f"From:    ", style="bold")
    body.append(f"{email.from_addr}\n")
    body.append(f"Subject: ", style="bold")
    body.append(f"{email.subject or '(no subject)'}\n")
    body.append(f"Date:    ", style="bold")
    body.append(f"{email.date or '?'}\n")
    body.append(f"Tag:     ", style="bold")
    body.append(f"{current_tag}", style=tag_color)
    body.append("\n")
    if reasoning:
        body.append(f"\n{reasoning[:200]}", style="dim")

    console.print(Panel(body, title=f"[dim]{idx}/{total}[/dim]", border_style="dim"))


def _getchar_prompt() -> str:
    """Read a single keypress. Falls back to line input when stdin is not a tty."""
    console.print("[c] confirm  [r] reclassify  [s] skip  [q] quit", markup=False)
    console.print("[bold]▸[/bold] ", end="")
    console.file.flush()
    if not sys.stdin.isatty():
        # Non-interactive (tests / piped input) — read a line
        line = sys.stdin.readline().strip().lower()
        return line[:1] if line else "s"
    try:
        import click
        key = click.getchar().lower()
        console.print(key)
        return key
    except Exception:
        line = input().strip().lower()
        return line[:1] if line else "s"


def _prompt_reclassify(current_tag: str) -> str | None:
    """Show tag menu, return chosen tag or None if cancelled."""
    tags = _get_all_tags()
    console.print("\n  Reclassify as:")
    for i, tag in enumerate(tags, 1):
        marker = " [dim](current)[/dim]" if tag == current_tag else ""
        console.print(f"  [cyan]{i}[/cyan]. {tag}{marker}")
    console.print(f"  [cyan]{len(tags) + 1}[/cyan]. [dim]enter new tag...[/dim]")
    console.print("  [dim]0. cancel[/dim]")
    console.print("\n  [bold]▸[/bold] ", end="")

    if not sys.stdin.isatty():
        line = sys.stdin.readline().strip()
    else:
        line = input().strip()

    if not line or line == "0":
        return None
    try:
        choice = int(line)
        if 1 <= choice <= len(tags):
            return tags[choice - 1]
        if choice == len(tags) + 1:
            console.print("  New tag name: ", end="")
            if not sys.stdin.isatty():
                new_tag = sys.stdin.readline().strip()
            else:
                new_tag = input().strip()
            return new_tag if new_tag else None
    except ValueError:
        # Allow typing the tag name directly (existing or new)
        if line:
            return line
    return None


def _propose_rules(corrections: list[_Correction]) -> int:
    """Analyze corrections, propose rules, return count of rules added."""
    console.print(f"\n[bold]Analyzing {len(corrections)} corrections...[/bold]")
    with console.status("Generating rule proposals..."):
        correction_dicts = [
            {
                "message_id": c.message_id,
                "wrong_tag": c.wrong_tag,
                "correct_tag": c.correct_tag,
                "subject": c.subject,
                "from_addr": c.from_addr,
            }
            for c in corrections
        ]
        proposals = suggest_rules(correction_dicts)

    if not proposals:
        console.print("[dim]No patterns found — not enough signal yet.[/dim]\n")
        return 0

    console.print(f"\n[bold]Found {len(proposals)} rule proposal(s):[/bold]\n")
    added = 0

    for proposal in proposals:
        rule_yaml = yaml.dump([proposal], default_flow_style=False, sort_keys=False).strip()
        console.print(Panel(
            f"[dim]{rule_yaml}[/dim]",
            title=f"[cyan]{proposal.get('name', 'proposed rule')}[/cyan]",
            border_style="cyan",
        ))
        console.print("  [bold]Add to rules.yaml?[/bold] [[green]y[/green]/[dim]n[/dim]] ", end="")

        if not sys.stdin.isatty():
            line = sys.stdin.readline().strip().lower()
        else:
            try:
                import click
                line = click.getchar().lower()
                console.print(line)
            except Exception:
                line = input().strip().lower()

        if line == "y":
            _append_rule(proposal)
            console.print(f"  [green]✓ Added:[/green] {proposal.get('name')}\n")
            added += 1
        else:
            console.print("  [dim]skipped[/dim]\n")

    return added


def _append_rule(rule: dict) -> None:
    """Append a new rule to rules.yaml without touching existing content.

    Serializes only the new rule and appends raw text to the file.
    The existing file is never parsed, so comments and formatting survive.
    """
    import re

    RULES_FILE.parent.mkdir(parents=True, exist_ok=True)

    rule_yaml = yaml.dump(
        [rule], default_flow_style=False, sort_keys=False, allow_unicode=True
    )

    if not RULES_FILE.exists():
        indented = "\n".join(
            f"  {line}" if line.strip() else "" for line in rule_yaml.splitlines()
        )
        RULES_FILE.write_text(f"rules:\n\n{indented}\n")
        return

    content = RULES_FILE.read_text()

    # Match the indent level of existing list items in the file
    match = re.search(r"^( *)- ", content, re.MULTILINE)
    indent = match.group(1) if match else "  "

    indented = "\n".join(
        f"{indent}{line}" if line.strip() else "" for line in rule_yaml.splitlines()
    )

    if not content.endswith("\n"):
        content += "\n"
    content += f"\n{indented}\n"
    RULES_FILE.write_text(content)


def _print_summary(report: TriageReport) -> None:
    console.print(
        f"\n[bold]Triage complete.[/bold] "
        f"Reviewed: [bold]{report.reviewed}[/bold]  "
        f"Confirmed: [green]{report.confirmed}[/green]  "
        f"Corrected: [cyan]{report.corrected}[/cyan]  "
        f"Skipped: [dim]{report.skipped}[/dim]"
        + (f"  Rules added: [bold green]{report.rules_added}[/bold green]" if report.rules_added else "")
    )
