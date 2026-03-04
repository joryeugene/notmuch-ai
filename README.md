# notmuch-ai

AI intelligence layer for notmuch email. Works with aerc, neomutt, himalaya, or any notmuch-based setup.

```
mbsync  →  notmuch new  →  notmuch-ai classify  →  aerc
```

Five tags, no server, no web UI. You provide the LLM key.

| Tag | Meaning |
|-----|---------|
| `needs-reply` | A real person wrote this and expects a response |
| `ai-noise` | Auto-generated or marketing, no action needed |
| `ai-urgent` | Deadline, time-sensitive, or from a senior stakeholder |
| `ai-fyi` | Informational, genuine value, no reply needed |
| `ai-follow-up` | Needs future attention, cannot act now |

Every decision is logged to SQLite. `notmuch-ai why <id>` tells you exactly why.

Cost: built-in classifiers use claude-haiku (~$3/month at 100 emails/day). Drafts use claude-sonnet (only on demand).

---

## Quickstart

Requires notmuch configured and indexed, and uv installed.

**Step 1: Install**

```bash
git clone https://github.com/joryeugene/notmuch-ai
cd notmuch-ai
just install
# or: uv tool install --editable .
```

**Step 2: Set your API key**

```bash
export ANTHROPIC_API_KEY=sk-ant-...
# add to ~/.zshrc or ~/.bashrc to persist
```

**Step 3: Run setup**

```bash
notmuch-ai setup
```

This creates `~/.config/notmuch-ai/rules.yaml` and appends all five AI virtual folders to your aerc queries file.

**Verify it works:**

```bash
just dry-run
# or: notmuch-ai classify --dry-run --verbose
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

Now every `notmuch new` (including mbsync) automatically classifies new mail.

---

## Daily workflow

```bash
# Inspect recent decisions
just audit
# or: notmuch-ai log -n 50

# Explain a specific decision
just why <message-id>
# or: notmuch-ai why id:abc123@mail.gmail.com

# Generate a reply draft
just draft <message-id>
# or: notmuch-ai draft id:abc123@mail.gmail.com

# Test rules against a specific message (no tags applied)
notmuch-ai rules check id:abc123@mail.gmail.com
```

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

Conditions are plain English evaluated by LLM. `static_subject` and `static_from` are regex fast-paths that skip the LLM entirely. Use them for high-volume, predictable patterns.

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

To generate drafts from aerc, add this to `~/.config/aerc/binds.conf`:

```ini
[messages]
gD = :pipe notmuch-ai draft -<Enter>

[view]
gd = :pipe -m notmuch-ai draft -<Enter>
```

Use `gd` while reading an email, or `gD` from the message list. The draft prints to a pager. Nothing is sent.

---

## Alternative LLM providers

Default is Anthropic (haiku for classification, sonnet for drafts). To use a different provider, set `NOTMUCH_AI_MODEL` to any [litellm-compatible model](https://docs.litellm.ai/docs/providers):

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
| `llm.py` | prompt → structured response |
| `rules.py` | email + rules → tag operations |
| `classify.py` | orchestrate: fetch → evaluate → tag → log |
| `triage.py` | interactive review: corrections → rule proposals |
| `draft.py` | message-id → reply draft text |
| `db.py` | append-only audit trail + corrections (SQLite) |
| `cli.py` | typer CLI, no business logic |

No component knows about any other. Swap the LLM provider without touching the rules engine. Replace the audit trail without touching classify.

---

## License

MIT
