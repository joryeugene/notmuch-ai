# notmuch-ai backlog

Future ideas, deferred deliberately. Approach B (five tags + triage) ships first.
These are Approach C and beyond.

---

## Thread Awareness

**What:** When classifying a reply, pass the full thread context to the LLM — not just the latest message.

**Why it matters:** A message that looks like `ai-noise` in isolation often becomes `needs-reply` when you see the thread. Single-message classification misreads conversational context.

**How:**
- `notmuch.py`: new `show_thread(thread_id)` fetches all messages in a thread
- `classify.py`: when the incoming message has an `In-Reply-To` header, pass full thread context
- Cost: ~3-5x token usage for threaded conversations

**Trade-off:** Higher cost, higher accuracy. Worth it for mailing lists, support threads, anything where a single message reverses the classification.

---

## Action Extraction

**What:** New `actions.py` module extracts tasks, deadlines, and questions from email body. Adds `ai-task` tag when actionable items are found.

**Why it matters:** `needs-reply` tells you someone wants a response. `ai-task` tells you what they actually want. The difference between "I should reply" and "I should reply by Friday with a budget number" is significant.

**How:**
- `actions.py`: LLM extracts structured `{task, deadline, who_asked}` from body text
- Output format: structured JSON, suitable for task manager integration
- Integration targets: Things, OmniFocus, plain text task files, notmuch virtual folders

---

## Daily Digest (`notmuch-ai digest`)

**What:** A structured summary of today's email, grouped by tag.

**Output:**
```
URGENT (2)
  - Budget approval deadline: respond by EOD — finance@company.com
  - Production incident: Sentry alert still active — alerts@sentry.io

NEEDS REPLY (4)
  - PR review requested by @alice — github.com
  - Question from manager on Q1 roadmap — jsmith@company.com
  ...

FYI (11) — 3 newsletters, 5 GitHub notifications, 3 announcements
NOISE (47 archived)
```

**How:**
- Group by tag, sort by urgency
- LLM-written prose summary for FYI emails (combine into one paragraph)
- Pipe target: terminal, email to self, Obsidian daily note
- Cron: `0 8 * * * notmuch-ai digest >> ~/digest.txt`

---

## Sender Reputation Tracking

**What:** Track reply rate per sender domain in the audit DB. Use it to calibrate classification confidence.

**Why:** Senders you always reply to should boost `needs-reply` confidence. Senders you always archive should boost `ai-noise` confidence. No new LLM calls — pure DB analytics.

**How:**
- New `reputation` table: `(domain, reply_count, archive_count, last_seen)`
- `classify.py`: after tagging, query reputation to adjust tag confidence
- `notmuch-ai log` shows reputation signals alongside decisions

---

## Confidence Calibration Over Time

**What:** After N weeks of corrections via `triage`, compute per-classifier accuracy. Surface calibration drift.

**Signals:**
- If `ai-noise` correction rate climbs above 20%, emit a warning in `classify` output
- `notmuch-ai log` shows per-tag accuracy based on correction history
- Long-term: detect systematic drift (new job, new email habits) and suggest re-tuning

---

## Rule Performance Analytics (`notmuch-ai rules stats`)

**What:** Per-rule hit counts, correction rates, and freshness.

**Output:**
```
Rule               Hits (30d)  Corrections  Last hit
Newsletter              1,204           2%  today
Cold outreach             87           8%  yesterday
PR review                 34           0%  3h ago
Security alert             2           0%  12 days ago  ← consider removing
```

**Value:** Identifies dead rules (zero hits in 30 days) and over-triggering rules (high correction rate). Prunes the rules.yaml over time.

---

## aerc Deep Integration

**What:** Tighter keybinding integration for in-context triage and tagging without leaving aerc.

**Ideas:**
- `gA` → pipe current email to `notmuch-ai triage --single` (triage open email without leaving aerc)
- `gF` → tag current email as `ai-follow-up` directly from message list
- Virtual folder `AI Follow-up` created automatically by `notmuch-ai setup`
- `notmuch-ai setup` adds all five AI virtual folders, not just three

---

## Alternative Provider Support

**What:** Per-rule model override and local model support.

notmuch-ai already uses litellm, so provider switching is partially there. The gap is per-rule granularity.

**Ideas:**
- `static_from`/`static_subject` rules: no LLM at all (already true)
- Urgent detection: use claude-sonnet (more accurate)
- Noise detection: use claude-haiku or local Ollama (cheapest)
- Config: `model: ollama/llama3` at the rule level

**Ollama use case:** Air-gapped environments, privacy-sensitive setups, zero-cost classification.

---

## `notmuch-ai triage --single <message-id>`

**What:** Triage a single email by message-id rather than reviewing the recent batch.

**Use case:** aerc integration — pipe the current email directly to triage from a keybinding without leaving the email view.

**How:** Extend `triage.run_triage_session()` to accept a `message_id` override. Skip the `db.recent()` fetch, show just that one email, prompt for correction, propose rules only if ≥2 corrections are accumulated in the DB.
