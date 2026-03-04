# notmuch-ai

AI intelligence layer for notmuch email. Works with aerc, neomutt, himalaya, or any notmuch-based setup.

```
mbsync  →  notmuch new  →  notmuch-ai classify  →  aerc
```

Three tags. No server. No web UI. Bring your own LLM key.

- `needs-reply`: a real person wrote this to you and expects a response
- `ai-noise`: auto-generated or marketing, no action needed
- `ai-urgent`: deadline, time-sensitive, or from a senior stakeholder

Every decision is logged to SQLite. `notmuch-ai why <id>` tells you exactly why.

Cost: built-in classifiers use claude-haiku (~$3/month at 100 emails/day). Drafts use claude-sonnet (only on demand).

---

## Quickstart

**Prerequisites:** notmuch configured and indexed, uv installed

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

Creates `~/.config/notmuch-ai/rules.yaml` and appends the three AI virtual folders to your aerc queries file.

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
needs-reply = tag:needs-reply AND NOT tag:replied AND NOT tag:deleted
ai-noise    = tag:ai-noise AND NOT tag:deleted
ai-urgent   = tag:ai-urgent AND NOT tag:deleted
```

Draft generation from aerc. Add to `~/.config/aerc/binds.conf`:

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
| `draft.py` | message-id → reply draft text |
| `db.py` | append-only audit trail (SQLite) |
| `cli.py` | typer CLI, no business logic |

No component knows about any other. Swap the LLM provider without touching the rules engine. Replace the audit trail without touching classify.

---

## License

MIT
