"""
CLI entrypoint.

Commands:
  sync       — full pipeline: IMAP sync → notmuch new → classify new mail
  classify   — classify messages and apply AI tags (backfill or custom query)
  why        — explain why a message was tagged
  draft      — generate a reply draft
  rules      — manage and test rules
  setup      — first-time setup (config, aerc queries, post-new hook)
  log        — show recent classification decisions
  triage     — interactive review of recent classifications, propose new rules
"""

from __future__ import annotations

import os
import shutil
import subprocess as _sp
from pathlib import Path
from typing import Optional

import yaml

import typer
from rich.console import Console
from rich.table import Table
from rich import print as rprint

from notmuch_ai import classify as classify_mod, db, draft as draft_mod
import notmuch_ai.triage as triage_mod
from notmuch_ai.rules import load_user_rules, RULES_FILE, CONFIG_DIR

CONFIG_FILE = CONFIG_DIR / "config.yaml"

app = typer.Typer(
    name="notmuch-ai",
    help="AI intelligence layer for notmuch email.",
    no_args_is_help=True,
)
console = Console()

rules_app = typer.Typer(help="Manage and test classification rules.")
app.add_typer(rules_app, name="rules")


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------

@app.command()
def classify(
    query: str = typer.Option(
        "tag:inbox AND NOT tag:ai-classified",
        "--query", "-q",
        help="Notmuch query to select messages for classification.",
    ),
    limit: Optional[int] = typer.Option(None, "--limit", "-n", help="Max messages to process."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would happen without applying tags."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show per-message decisions."),
    workers: int = typer.Option(1, "--workers", "-w", help="Parallel LLM workers for backfill (default: sequential)."),
) -> None:
    """Classify messages and apply AI tags (needs-reply, ai-noise, ai-urgent)."""
    if dry_run:
        rprint("[yellow]DRY RUN — no tags will be applied[/yellow]")

    if verbose:
        report = classify_mod.classify_messages(
            query=query, limit=limit, dry_run=dry_run,
            verbose=verbose, workers=workers,
        )
    else:
        with console.status("Classifying messages..."):
            report = classify_mod.classify_messages(
                query=query, limit=limit, dry_run=dry_run,
                verbose=verbose, workers=workers,
            )

    if report.paused:
        rprint("[yellow]AI classification is paused.[/yellow] Run [cyan]notmuch-ai resume[/cyan] to re-enable.")
        return

    rprint(
        f"[green]Done.[/green] "
        f"Processed: [bold]{report.processed}[/bold]  "
        f"Tagged: [bold cyan]{report.tagged}[/bold cyan]  "
        f"No match: {report.skipped}  "
        f"Errors: [red]{report.errors}[/red]"
    )

    if report.static_only:
        rprint(
            "[yellow]No LLM available — static rules only.[/yellow] "
            "Set ANTHROPIC_API_KEY or install claude CLI for full classification."
        )


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------

def _load_sync_command() -> str | None:
    """Return the configured IMAP sync command from config.yaml, or None."""
    if not CONFIG_FILE.exists():
        return None
    try:
        data = yaml.safe_load(CONFIG_FILE.read_text()) or {}
        cmd = data.get("sync_command", "")
        return cmd.strip() or None
    except Exception:
        return None


@app.command()
def sync(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would happen without applying tags."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show per-message decisions."),
    workers: int = typer.Option(1, "--workers", "-w", help="Parallel LLM workers (default: sequential)."),
) -> None:
    """Full mail pipeline: sync tool → notmuch new → classify new mail."""
    from notmuch_ai import notmuch

    # 1. Run IMAP sync tool if configured (pause does not block this step)
    sync_cmd = _load_sync_command()
    if sync_cmd:
        rprint(f"[dim]Running:[/dim] {sync_cmd}")
        result = _sp.run(sync_cmd, shell=True)
        if result.returncode != 0:
            rprint(f"[red]Sync command failed (exit {result.returncode})[/red]")
            raise typer.Exit(result.returncode)

    # 2. Index new mail
    rprint("[dim]Indexing new mail...[/dim]")
    new_count = notmuch.new()
    if new_count:
        rprint(f"[dim]Indexed {new_count} new message(s)[/dim]")

    # 3. Classify new arrivals (idempotent: ai-classified tag prevents double-processing)
    if dry_run:
        rprint("[yellow]DRY RUN — no tags will be applied[/yellow]")

    new_query = "tag:new AND tag:inbox AND NOT tag:ai-classified"
    if verbose:
        report = classify_mod.classify_messages(
            query=new_query, dry_run=dry_run, verbose=verbose, workers=workers,
        )
    else:
        with console.status("Classifying new mail..."):
            report = classify_mod.classify_messages(
                query=new_query, dry_run=dry_run, verbose=verbose, workers=workers,
            )

    if report.paused:
        rprint("[yellow]AI classification is paused.[/yellow] Run [cyan]notmuch-ai resume[/cyan] to re-enable.")
        return

    # 4. Summary
    rprint(
        f"[green]Done.[/green] "
        f"New: [bold]{report.processed}[/bold]  "
        f"Tagged: [bold cyan]{report.tagged}[/bold cyan]  "
        f"No match: {report.skipped}  "
        f"Errors: [red]{report.errors}[/red]"
    )

    if report.static_only:
        rprint(
            "[yellow]No LLM available — static rules only.[/yellow] "
            "Set ANTHROPIC_API_KEY or install claude CLI for full classification."
        )

    remaining = classify_mod.count_unclassified()
    if remaining > 0:
        rprint(f"  [dim]Backfill remaining: {remaining:,} — run [cyan]notmuch-ai classify --limit 200[/cyan][/dim]")


# ---------------------------------------------------------------------------
# why
# ---------------------------------------------------------------------------

@app.command()
def why(
    message_id: str = typer.Argument(..., help="Notmuch message-id (with or without 'id:' prefix)."),
) -> None:
    """Explain why a message was tagged the way it was."""
    decisions = db.why(message_id)
    if not decisions:
        rprint(f"[yellow]No classification history found for {message_id}[/yellow]")
        raise typer.Exit(1)

    table = Table(title=f"Classification history: {message_id}", show_lines=True)
    table.add_column("Time", style="dim", width=24)
    table.add_column("Rule", style="cyan")
    table.add_column("Tags Added", style="green")
    table.add_column("Reasoning")
    table.add_column("Dry run", style="dim", width=8)

    for d in decisions:
        table.add_row(
            d["ts"][:19],
            d["rule"],
            " ".join(d["tags_added"]) or "—",
            d.get("llm_response") or d.get("condition") or "—",
            "yes" if d["dry_run"] else "no",
        )

    console.print(table)


# ---------------------------------------------------------------------------
# draft
# ---------------------------------------------------------------------------

@app.command()
def draft(
    message_id: str = typer.Argument(..., help="Message-id of the email to reply to. Use '-' to read Message-Id from stdin (aerc :pipe integration)."),
    context: str = typer.Option("", "--context", "-c", help="Additional context for the draft."),
) -> None:
    """Generate a reply draft and print it to stdout."""
    import re
    import sys

    if message_id == "-":
        raw = sys.stdin.read()
        m = re.search(r"^[Mm]essage-[Ii][Dd]:\s*<?([^>\s\r\n]+)", raw, re.MULTILINE)
        if not m:
            rprint("[red]Error:[/red] Could not extract Message-Id from stdin")
            raise typer.Exit(1)
        message_id = m.group(1)

    try:
        text = draft_mod.generate(message_id, context=context)
        print(text)
    except ValueError as e:
        rprint(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------

@app.command()
def log(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of recent decisions to show."),
) -> None:
    """Show recent AI classification decisions."""
    decisions = db.recent(limit=limit)
    if not decisions:
        rprint("[yellow]No decisions logged yet.[/yellow]")
        return

    table = Table(title="Recent classifications", show_lines=False)
    table.add_column("Time", style="dim", width=19)
    table.add_column("Subject", max_width=40)
    table.add_column("Rule", style="cyan", max_width=25)
    table.add_column("Tags", style="green")
    table.add_column("DR", style="dim", width=4)

    for d in decisions:
        table.add_row(
            d["ts"][:19],
            (d["subject"] or "")[:40],
            d["rule"],
            " ".join(d["tags_added"]) or "—",
            "✓" if d["dry_run"] else "",
        )

    console.print(table)


# ---------------------------------------------------------------------------
# rules subcommands
# ---------------------------------------------------------------------------

@rules_app.command("list")
def rules_list() -> None:
    """Show all user-defined rules."""
    user_rules = load_user_rules()
    if not user_rules:
        rprint(f"[yellow]No rules found.[/yellow] Create {RULES_FILE} to add rules.")
        rprint("\nExample rule:")
        rprint('[dim]  - name: "Sales pitch"[/dim]')
        rprint('[dim]    condition: "Is this a sales or marketing email from someone I don\'t know?"[/dim]')
        rprint('[dim]    action: tag add ai-cold-outreach[/dim]')
        return

    table = Table(title=f"User rules ({RULES_FILE})", show_lines=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("Name", style="cyan")
    table.add_column("Type", style="yellow", width=7)
    table.add_column("Condition / Patterns")
    table.add_column("Action", style="green")

    for i, r in enumerate(user_rules, 1):
        has_patterns = bool(r.static_from or r.static_subject)
        if has_patterns and r.condition:
            rule_type = "hybrid"
        elif has_patterns:
            rule_type = "static"
        else:
            rule_type = "LLM"

        detail_lines = []
        if r.condition:
            detail_lines.append(r.condition)
        for p in r.static_from:
            detail_lines.append(f"[dim]from: {p}[/dim]")
        for p in r.static_subject:
            detail_lines.append(f"[dim]subj: {p}[/dim]")

        parts = [f"+{t}" for t in r.action_add] + [f"-{t}" for t in r.action_remove]
        action = " ".join(parts)
        table.add_row(str(i), r.name, rule_type, "\n".join(detail_lines), action.strip())

    console.print(table)


@rules_app.command("check")
def rules_check(
    message_id: str = typer.Argument(..., help="Message-id to test rules against."),
    verbose: bool = typer.Option(True, "--verbose/--quiet", help="Show reasoning."),
) -> None:
    """Test rules against a specific message (no tags applied)."""
    from notmuch_ai import notmuch
    from notmuch_ai.rules import evaluate

    email = notmuch.show(message_id)
    if not email:
        rprint(f"[red]Message not found:[/red] {message_id}")
        raise typer.Exit(1)

    rprint(f"[bold]Checking:[/bold] {email.subject!r} from {email.from_addr}")
    rprint()

    matches = evaluate(
        from_addr=email.from_addr,
        subject=email.subject,
        body=email.body_text,
        tags=email.tags,
    )

    if not matches:
        rprint("[yellow]No rules matched.[/yellow]")
        return

    for m in matches:
        tags_str = " ".join(f"[green]+{t}[/green]" for t in m.tags.add)
        tags_str += " ".join(f"[red]-{t}[/red]" for t in m.tags.remove)
        rprint(f"  [cyan]{m.rule_name}[/cyan] → {tags_str}")
        if verbose and m.reasoning:
            rprint(f"    [dim]{m.reasoning}[/dim]")


# ---------------------------------------------------------------------------
# pause / resume / status
# ---------------------------------------------------------------------------

_PAUSE_FLAG = CONFIG_DIR / ".paused"


def _render_heatmap(counts: list[int]) -> str:
    """Render a 5-level density heatmap string from a list of hourly counts.

    Levels: · ░ ▒ ▓ █  (empty → sparse → dense)
    Scale is relative to the busiest hour so any activity level is readable.
    """
    CHARS = "·░▒▓█"
    max_count = max(counts) if any(counts) else 0
    if max_count == 0:
        return CHARS[0] * len(counts)
    return "".join(CHARS[min(4, round(c / max_count * 4))] for c in counts)


@app.command()
def pause() -> None:
    """Pause AI classification. Survives reboots. Applies immediately on the next mail sync."""
    _PAUSE_FLAG.touch()
    rprint("[yellow]Paused.[/yellow] notmuch-ai will not classify until you run [cyan]notmuch-ai resume[/cyan].")


@app.command()
def resume() -> None:
    """Resume AI classification after a pause."""
    if _PAUSE_FLAG.exists():
        _PAUSE_FLAG.unlink()
        rprint("[green]Resumed.[/green] AI classification is active.")
    else:
        rprint("[dim]Already active.[/dim] notmuch-ai is not paused.")


@app.command()
def status() -> None:
    """Show current classification state: progress, rules, model, and API key."""
    paused = _PAUSE_FLAG.exists()

    # --- pending new + classified / remaining ---
    pending_new = classify_mod.count_pending_new()
    total_classified = db.count_classified()
    unclassified_remaining = classify_mod.count_unclassified()
    total = total_classified + unclassified_remaining
    pct = int(total_classified / total * 100) if total else 0
    bar_filled = pct // 5
    bar = "█" * bar_filled + "░" * (20 - bar_filled)

    # --- last run ---
    last_run = db.last_run_time()
    last_run_str = last_run[:19].replace("T", " ") if last_run else "never"

    # --- rules ---
    user_rules = load_user_rules()

    # --- model / provider ---
    from notmuch_ai.llm import _provider
    model = os.environ.get("NOTMUCH_AI_MODEL", "claude-haiku-4-5 (default)")

    provider = _provider()
    if provider == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        provider_status = f"[green]Anthropic API[/green] ({api_key[:8]}...)"
    elif provider == "claude-cli":
        provider_status = "[yellow]Claude CLI[/yellow] (subscription, slower)"
    else:
        provider_status = "[red]none[/red] (static rules only)"

    # --- activity windows + heatmap ---
    counts_1h  = db.count_classified_window(1)
    counts_4h  = db.count_classified_window(4)
    counts_24h = db.count_classified_window(24)
    heatmap    = _render_heatmap(db.hourly_counts(24))

    # --- recent errors ---
    recent_errors = db.count_recent_errors()

    # --- render ---
    state_str = "[red]PAUSED[/red]" if paused else "[green]active[/green]"
    rprint(f"\n[bold]notmuch-ai status[/bold]")
    rprint(f"  State:       {state_str}")
    rprint(f"  Last run:    {last_run_str}")
    rprint(f"  Pending new: {pending_new:,}")
    rprint()
    rprint(f"  [bold]Backfill progress[/bold]")
    rprint(f"  [{bar}] {pct}%")
    rprint(f"  Classified:  {total_classified:,}")
    rprint(f"  Remaining:   {unclassified_remaining:,}")
    rprint(f"  Total:       {total:,}")
    rprint()
    rprint(f"  [bold]Activity[/bold]")
    rprint(f"  1h: {counts_1h:>4}   4h: {counts_4h:>4}   24h: {counts_24h:>4}")
    rprint(f"  [{heatmap}]  \u2190 now")
    rprint()
    rprint(f"  Rules:       {len(user_rules)} user rules loaded from {RULES_FILE}")
    rprint(f"  Model:       {model}")
    rprint(f"  Provider:    {provider_status}")
    rprint(f"  Errors (24h): {recent_errors}")
    if paused:
        rprint()
        rprint(f"  Run [cyan]notmuch-ai resume[/cyan] to re-enable classification.")
    rprint()


# ---------------------------------------------------------------------------
# triage
# ---------------------------------------------------------------------------

@app.command()
def triage(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of recent decisions to review."),
) -> None:
    """Interactive review of recent classifications. Correct mistakes and auto-generate rules."""
    triage_mod.run_triage_session(limit=limit)


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------

@app.command()
def setup() -> None:
    """First-time setup: create config, aerc query files, post-new hook."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Example rules.yaml
    if not RULES_FILE.exists():
        example = Path(__file__).parent.parent / "config" / "rules.example.yaml"
        if example.exists():
            shutil.copy(example, RULES_FILE)
            rprint(f"[green]Created[/green] {RULES_FILE}")
        else:
            RULES_FILE.write_text(_EXAMPLE_RULES)
            rprint(f"[green]Created[/green] {RULES_FILE}")
    else:
        rprint(f"[dim]Exists[/dim] {RULES_FILE}")

    # 2. aerc query file (single key=value file, not a directory)
    AI_QUERIES = {
        "needs-reply": "tag:needs-reply AND NOT tag:replied AND NOT tag:deleted",
        "ai-noise": "tag:ai-noise AND NOT tag:deleted",
        "ai-urgent": "tag:ai-urgent AND NOT tag:deleted",
    }

    xdg = os.environ.get("XDG_CONFIG_HOME")
    aerc_queries = Path(xdg) / "aerc" / "queries" if xdg else Path.home() / ".config" / "aerc" / "queries"
    if not aerc_queries.is_file():
        aerc_queries = Path.home() / "Library" / "Preferences" / "aerc" / "queries"

    if aerc_queries.is_file():
        existing = aerc_queries.read_text()
        added: list[str] = []
        for name, query in AI_QUERIES.items():
            if f"{name} " not in existing and f"{name}=" not in existing:
                existing = existing.rstrip("\n") + f"\n{name} = {query}\n"
                added.append(name)
        if added:
            aerc_queries.write_text(existing)
            rprint(f"[green]Added to[/green] {aerc_queries}: {', '.join(added)}")
        else:
            rprint(f"[dim]Already present[/dim] in {aerc_queries}")
    else:
        rprint(f"[yellow]aerc queries file not found[/yellow] — add these manually:")
        for name, query in AI_QUERIES.items():
            rprint(f"  {name} = {query}")

    # 3. post-new hook — use notmuch's configured database path, not a hardcoded guess
    try:
        db_path = _sp.run(
            ["notmuch", "config", "get", "database.path"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        hook_dir = Path(db_path) / ".notmuch" / "hooks" if db_path else Path.home() / ".mail" / ".notmuch" / "hooks"
    except Exception:
        hook_dir = Path.home() / ".mail" / ".notmuch" / "hooks"
    hook_file = hook_dir / "post-new"
    if hook_dir.exists():
        if not hook_file.exists():
            hook_file.write_text(_POST_NEW_HOOK)
            hook_file.chmod(0o755)
            rprint(f"[green]Created[/green] {hook_file}")
        else:
            rprint(f"[dim]Exists[/dim] {hook_file} — add this line if not present:")
            rprint("  notmuch-ai classify")
    else:
        rprint(f"[yellow]Hook dir not found[/yellow] ({hook_dir}) — create it and add:")
        rprint("  notmuch-ai classify")

    # 4. Sync command (optional)
    rprint()
    existing_cmd = _load_sync_command() or ""
    prompt_text = f"IMAP sync command (e.g. mbsync -a, leave blank to skip)"
    if existing_cmd:
        prompt_text += f" [{existing_cmd}]"
    sync_cmd = typer.prompt(prompt_text, default=existing_cmd or "")
    if sync_cmd.strip():
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        config_data: dict = {}
        if CONFIG_FILE.exists():
            config_data = yaml.safe_load(CONFIG_FILE.read_text()) or {}
        config_data["sync_command"] = sync_cmd.strip()
        CONFIG_FILE.write_text(yaml.dump(config_data, default_flow_style=False))
        rprint(f"[green]Saved[/green] {CONFIG_FILE}")
        rprint(f"  [dim]notmuch-ai sync will run:[/dim] {sync_cmd.strip()} → notmuch new → classify")
    else:
        rprint("[dim]No sync command set — notmuch-ai sync will run notmuch new + classify only.[/dim]")

    rprint()
    rprint("[bold green]Setup complete.[/bold green] Run [cyan]notmuch-ai sync --dry-run[/cyan] to test.")


_EXAMPLE_RULES = """\
# notmuch-ai rules
# Conditions are evaluated by LLM. Be specific and natural.
# Actions: "tag add <tag>" or "tag remove <tag>"
# Optional static_from/static_subject for fast-path matching (no LLM).

rules:
  - name: "Cold outreach"
    condition: "Is this a sales or marketing email from someone I don't know personally?"
    action: tag add ai-cold-outreach
    static_subject:
      - "(?i)quick question"
      - "(?i)partnership opportunity"

  - name: "PR review request"
    condition: "Is this asking me personally to review a pull request?"
    action: tag add needs-reply

  - name: "Interview or hiring"
    condition: "Is this related to a job application, interview, or hiring decision that I need to act on?"
    action: tag add ai-urgent
"""

_POST_NEW_HOOK = """\
#!/usr/bin/env bash
# notmuch post-new hook — classify new arrivals only
notmuch-ai classify --query "tag:new AND tag:inbox AND NOT tag:ai-classified"
"""
