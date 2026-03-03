# notmuch-ai development workflows
# https://github.com/casey/just

PROJECT := "notmuch-ai"
AUDIT_DB := `echo ~/.local/share/notmuch-ai/audit.db`

# List all recipes
default:
    @just --list

# Install globally (makes notmuch-ai available on PATH)
install:
    uv tool install --editable .
    @echo "✓ notmuch-ai installed — run 'notmuch-ai --help' to verify"

# Install for development (editable, in project venv)
dev:
    uv pip install -e .

# First-time setup (aerc queries, rules.yaml, post-new hook)
setup:
    notmuch-ai setup

# Run unit tests
test:
    uv run pytest tests/ -v --ignore=tests/test_live.py

# Run unit tests + live API tests (requires ANTHROPIC_API_KEY)
test-all:
    uv run pytest tests/ -v -m "not skip"

# Run live classification test only
test-live:
    uv run pytest tests/test_live.py -v -s

# Dry-run classify inbox (last 50 messages, show decisions)
dry-run limit="50":
    notmuch-ai classify --query "tag:inbox AND NOT tag:ai-classified" --limit {{limit}} --dry-run --verbose

# Dry-run on already-classified messages (recheck)
recheck limit="20":
    notmuch-ai classify --query "tag:inbox" --limit {{limit}} --dry-run --verbose

# Show recent classification audit log
audit n="50":
    notmuch-ai log -n {{n}}

# Explain why a specific message was tagged (pass ID=...)
why ID:
    notmuch-ai why {{ID}}

# Generate a reply draft (pass ID=...)
draft ID:
    notmuch-ai draft {{ID}}

# List configured rules
rules:
    notmuch-ai rules list

# Test rules against a specific message without applying tags (pass ID=...)
check ID:
    notmuch-ai rules check {{ID}}

# Reset audit database (clears all classification history)
clean:
    @rm -f {{AUDIT_DB}} && echo "✓ Audit database cleared"

# Show project info
info:
    @echo "Model (classify): ${NOTMUCH_AI_MODEL:-claude-haiku-4-5-20251001}"
    @echo "Model (draft):    ${NOTMUCH_AI_DRAFT_MODEL:-claude-sonnet-4-6}"
    @echo "Config:           ~/.config/notmuch-ai/rules.yaml"
    @echo "Audit DB:         {{AUDIT_DB}}"
    @notmuch-ai --version 2>/dev/null || echo "notmuch-ai not on PATH — run: just install"
