# notmuch-ai

Your inbox is full of things an LLM already knows what to do with. notmuch-ai does that work. It classifies every incoming email, tags it, and exits. It has no server, no web UI, and no background process.

Works with aerc, neomutt, himalaya, or any notmuch-based setup. Uses an Anthropic API key for speed (~1s/email), or works with just a Claude Code subscription (no API key needed, ~5-10s/email). Falls back to static regex rules when neither is available.

## How it works

```
First-time setup:
  notmuch-ai setup
    └── configures sync tool, installs post-new hook, creates rules.yaml

Daily (automatic via post-new hook, or run manually):
  notmuch-ai sync
    ├── [mbsync -a]          # your IMAP sync tool (if configured)
    ├── notmuch new          # index new mail
    ├── classify new mail    # tag:new AND tag:inbox AND NOT tag:ai-classified
    └── status summary       # new processed, tagged, errors

Check anytime:
  notmuch-ai status          # pending new, backfill progress, provider, errors

Optional backfill (classify old mail):
  notmuch-ai classify --limit 500 --workers 3
```

## Tags

| Tag | Meaning |
|-----|---------|
| `needs-reply` | A real person wrote this and expects a response |
| `ai-noise` | Auto-generated or marketing, no action needed |
| `ai-urgent` | Deadline, time-sensitive, or from a senior stakeholder |
| `ai-fyi` | Informational, genuine value, no reply needed |
| `ai-follow-up` | Needs future attention, cannot act now |

Every decision is logged to SQLite. `notmuch-ai why <id>` tells you exactly why a message was tagged.

Other tools require a server, a web UI, or vendor lock-in. notmuch-ai is a Unix command. It runs when mail arrives, does its job, and exits. You own every piece: the rules, the tags, and the audit trail.

---

## Quickstart

Requires notmuch configured and indexed, and uv installed.

**Step 1: Install**

```bash
git clone https://github.com/joryeugene/notmuch-ai
cd notmuch-ai
uv tool install --editable .
```

**Step 2: Choose your provider**

```bash
# Option A: Anthropic API key (fast, ~1s per email)
export ANTHROPIC_API_KEY=sk-ant-...
# Add to ~/.zshrc or ~/.bashrc to persist

# Option B: Skip this step if you have Claude Code installed
# notmuch-ai detects the claude CLI and uses it automatically (~5-10s per email)
```

**Step 3: Run setup**

```bash
notmuch-ai setup
```

This creates `~/.config/notmuch-ai/rules.yaml`, installs the post-new hook, appends the five AI virtual folders to your aerc queries file, and optionally configures your IMAP sync tool (e.g. `mbsync -a`).

**Verify it works:**

```bash
notmuch-ai sync --dry-run --verbose
```

---

## Automating mail sync

**Option A: `notmuch-ai sync` (recommended)**

Run `notmuch-ai sync` from a cron job, launchd, or your terminal. It chains your IMAP sync tool, `notmuch new`, and classification in one command. Configure your sync tool once during setup:

```bash
notmuch-ai setup   # prompts: "IMAP sync command (e.g. mbsync -a)"
notmuch-ai sync    # runs the full pipeline
```

**Option B: post-new hook (automatic on every `notmuch new`)**

`notmuch-ai setup` installs this hook automatically. To add it manually, append to `~/.mail/.notmuch/hooks/post-new`:

```bash
# AI classification: new arrivals only
if command -v notmuch-ai &>/dev/null; then
  notmuch-ai classify --query "tag:new AND tag:inbox AND NOT tag:ai-classified"
fi
```

```bash
chmod +x ~/.mail/.notmuch/hooks/post-new
```

`tag:new` is applied by `notmuch new` to messages indexed in this sync. The hook sees only those messages, not your historical backlog. notmuch-ai runs only when mail arrives. It is not a daemon or background process.

**Upgrading from an earlier version?** If your hook already has `notmuch-ai classify` with no arguments, add `--query "tag:new AND tag:inbox AND NOT tag:ai-classified"` to it. Without this flag, the hook classifies your entire unclassified inbox on every sync.

---

## Pause and resume

Use `notmuch-ai pause` to stop all AI classification without editing the hook manually. The pause survives reboots. Use it when you want to take a break from classification entirely, test rule changes, or investigate unexpected tags.

```bash
notmuch-ai pause    # stop classifying on next mail sync
notmuch-ai resume   # re-enable
notmuch-ai status   # show current state, backfill progress, and provider
```

`notmuch-ai status` shows how many messages have been classified, how many remain, and which rules and model are active. Use it to track backfill progress.

---

## Backfilling an existing inbox

The post-new hook only classifies new arrivals. Historical messages that arrived before you installed notmuch-ai need a one-time backfill.

**How it tracks progress.** Every processed message gets an `ai-classified` tag. The default backfill query is `tag:inbox AND NOT tag:ai-classified`, so each run picks up exactly where the last one left off. Messages are processed newest-first, so your recent inbox gets classified before older mail. If a batch is interrupted, no work is lost.

The hook and backfill use separate queries and do not interfere with each other. You can run backfill batches while mail syncs normally.

```bash
# Preview the 10 most recent unclassified messages without applying tags
notmuch-ai classify --dry-run --verbose --limit 10

# Classify in batches with parallel LLM calls (3-5 workers recommended)
notmuch-ai classify --limit 200 --workers 3
notmuch-ai status   # check progress: classified vs remaining

# Repeat until "Remaining: 0"
```

`--limit 200` processes the 200 most recent unclassified emails. Each run picks up where the last left off because classified messages get an `ai-classified` tag and drop out of the next query. `--workers 3` runs 3 LLM calls in parallel while keeping tag writes sequential.

---

## Daily workflow

```bash
# Full pipeline: IMAP sync + notmuch new + classify new mail
notmuch-ai sync

# Show current state: pending new, backfill progress, provider, errors
notmuch-ai status

# Preview without applying any tags
notmuch-ai sync --dry-run --verbose

# Inspect recent decisions
notmuch-ai log -n 50

# Explain a specific decision
notmuch-ai why id:abc123@mail.gmail.com

# Generate a reply draft
notmuch-ai draft id:abc123@mail.gmail.com

# Test rules against a specific message (no tags applied)
notmuch-ai rules check id:abc123@mail.gmail.com
```

`--dry-run` shows every classification decision without writing any tags to notmuch. `--verbose` adds per-message reasoning from the LLM. Run both together before your first real classify to confirm the rules are doing what you expect.

---

## Triage: close the feedback loop

Over time the classifier makes mistakes. `notmuch-ai triage` lets you review recent decisions one at a time and correct them:

```
$ notmuch-ai triage

┌─ 1/8 ─────────────────────────────────────────────────────────┐
│ From:    eng-announce@company.com                              │
│ Subject: Q1 architecture review notes                          │
│ Date:    2026-03-04 09:12                                      │
│ Tag:     ai-noise                                              │
│                                                                │
│ Appears informational, no reply requested...                   │
└────────────────────────────────────────────────────────────────┘
▸
```

Press `c` to confirm, `r` to reclassify, `s` to skip, or `q` to quit.

When you reclassify, you choose the correct tag from a numbered list. After the session, if you made two or more corrections, the LLM analyzes the pattern and proposes new rules:

```
Found 1 rule proposal:

  company-announcements
  ─────────────────────
  name: company-announcements
  condition: Is this a company-wide announcement from an internal
    address that I should read but don't need to act on?
  action: tag add ai-fyi

  Add to rules.yaml? [y/n]
```

Approved rules are appended to `~/.config/notmuch-ai/rules.yaml` immediately.

---

## Custom rules

Edit `~/.config/notmuch-ai/rules.yaml`:

```yaml
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
```

Conditions are plain English evaluated by the LLM. `static_subject` and `static_from` are regex fast-paths that skip the LLM entirely. Use them for high-volume, predictable patterns.

---

## aerc integration

Virtual folders are added automatically by `notmuch-ai setup`. They appear in aerc's sidebar:

```ini
needs-reply  = tag:needs-reply AND NOT tag:replied AND NOT tag:deleted
ai-noise     = tag:ai-noise AND NOT tag:deleted
ai-urgent    = tag:ai-urgent AND NOT tag:deleted
ai-fyi       = tag:ai-fyi AND NOT tag:deleted
ai-follow-up = tag:ai-follow-up AND NOT tag:deleted
```

To generate drafts from aerc, add this to `binds.conf`:

```ini
[messages]
gD = :pipe notmuch-ai draft -<Enter>

[view]
gd = :pipe -m notmuch-ai draft -<Enter>
```

Use `gd` while reading an email, or `gD` from the message list. The draft prints to a pager. Nothing is sent.

---

## Provider modes

notmuch-ai auto-detects the best available provider on every run. No configuration needed beyond setting (or not setting) an API key.

| Mode | Requirement | Speed | Cost |
|------|------------|-------|------|
| Anthropic API | `ANTHROPIC_API_KEY` set | ~1s/email | ~$3/mo at 100 emails/day |
| Claude CLI | Claude Code installed, no API key | ~5-10s/email | Included in subscription |
| Static-only | Neither | Instant | Free (regex rules only) |

Set the API key for speed. Remove it and the system falls back to `claude -p`. Remove Claude Code and it runs regex-only rules. `notmuch-ai status` shows which provider is active.

## Alternative LLM models

The default is Anthropic (haiku for classification, sonnet for drafts). To use a different model, set `NOTMUCH_AI_MODEL` to any [litellm-compatible model](https://docs.litellm.ai/docs/providers):

```bash
export NOTMUCH_AI_MODEL=gpt-4o-mini    # OpenAI
export NOTMUCH_AI_MODEL=ollama/llama3  # local Ollama (free)
```

---

## Development

```bash
just test       # unit tests (no API key needed)
just test-live  # live integration tests (requires ANTHROPIC_API_KEY)
just dry-run    # classify last 50 inbox messages, print reasoning
```

---

## Architecture

Each component has one job and no dependencies on the others:

| File | Job |
|------|-----|
| `notmuch.py` | subprocess wrapper: search, show, tag |
| `llm.py` | prompt to structured response |
| `rules.py` | email plus rules to tag operations |
| `classify.py` | orchestrate: fetch, evaluate, tag, log |
| `triage.py` | interactive review: corrections to rule proposals |
| `draft.py` | message-id to reply draft text |
| `db.py` | append-only audit trail and corrections (SQLite) |
| `cli.py` | Typer CLI, no business logic; `sync` orchestrates full pipeline |

No component knows about any other. Swap the LLM provider without touching the rules engine. Replace the audit trail without touching classify.

---

## License

MIT
