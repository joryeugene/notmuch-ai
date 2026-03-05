# notmuch-ai

Your inbox is full of things an LLM already knows what to do with. notmuch-ai does that work. It classifies every incoming email, tags it, and exits. It has no server, no web UI, and no background process.

```
mbsync  →  notmuch new  →  notmuch-ai classify  →  aerc
```

Works with aerc, neomutt, himalaya, or any notmuch-based setup. You provide the API key.

## Tags

| Tag | Meaning |
|-----|---------|
| `needs-reply` | A real person wrote this and expects a response |
| `ai-noise` | Auto-generated or marketing, no action needed |
| `ai-urgent` | Deadline, time-sensitive, or from a senior stakeholder |
| `ai-fyi` | Informational, genuine value, no reply needed |
| `ai-follow-up` | Needs future attention, cannot act now |

Every decision is logged to SQLite. `notmuch-ai why <id>` tells you exactly why a message was tagged.

Cost: built-in classifiers use claude-haiku (~$3/month at 100 emails/day). Drafts use claude-sonnet and only run on demand.

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

**Step 2: Set your API key**

```bash
export ANTHROPIC_API_KEY=sk-ant-...
# Add to ~/.zshrc or ~/.bashrc to persist
```

**Step 3: Run setup**

```bash
notmuch-ai setup
```

This creates `~/.config/notmuch-ai/rules.yaml` and appends the five AI virtual folders to your aerc queries file.

**Verify it works:**

```bash
notmuch-ai classify --dry-run --verbose
```

---

## Automating with post-new hook

Add to `~/.mail/.notmuch/hooks/post-new` (after all your existing notmuch tag rules):

```bash
# AI classification: runs after all static rules
if command -v notmuch-ai &>/dev/null; then
  notmuch-ai classify
fi
```

```bash
chmod +x ~/.mail/.notmuch/hooks/post-new
```

Every `notmuch new` (including mbsync) automatically classifies new mail. notmuch-ai runs only when mail arrives. It is not a daemon or background process.

---

## Pause and resume

Use `notmuch-ai pause` to stop AI classification without editing the hook manually. The pause survives reboots.

```bash
notmuch-ai pause    # stop classifying on next mail sync
notmuch-ai resume   # re-enable
notmuch-ai status   # show current state, backfill progress, and API key
```

`notmuch-ai status` shows how many messages have been classified, how many remain, and which rules and model are active. Use it to track backfill progress.

---

## Backfilling an existing inbox

If you have thousands of unclassified messages, process them in batches to stay within API rate limits.

```bash
# Verify you are using your own API key before backfilling
echo $ANTHROPIC_API_KEY

# Preview the first 10 messages before touching anything
notmuch-ai classify --dry-run --verbose --limit 10

# Pause automatic classification during manual backfill
notmuch-ai pause

# Classify in safe batches
notmuch-ai classify --limit 200
notmuch-ai status   # check progress

# Repeat until "Remaining: 0", then resume
notmuch-ai resume
```

---

## Daily workflow

```bash
# Show current state: progress, rules, model, API key
notmuch-ai status

# Preview classification without applying any tags
notmuch-ai classify --dry-run --verbose

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

## Alternative LLM providers

The default is Anthropic (haiku for classification, sonnet for drafts). To use a different provider, set `NOTMUCH_AI_MODEL` to any [litellm-compatible model](https://docs.litellm.ai/docs/providers):

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
| `cli.py` | Typer CLI, no business logic |

No component knows about any other. Swap the LLM provider without touching the rules engine. Replace the audit trail without touching classify.

---

## License

MIT
