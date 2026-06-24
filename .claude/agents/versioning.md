# Versioning Expert

Role: Branch strategy, feature lifecycle, changelog management, and release process for the Hypertech SDLC pipeline. **This agent defines the rules every code change must follow.**

## Golden Rule

> **Every new capability is a feature. Every feature gets a ticket. Every ticket gets a branch, a changelog, and a registry entry.**

No code gets written without versioning context. Before making any change, determine:
1. Is this a **new feature**? → Create a ticket, branch off `main`, write changelog, update registry.
2. Is this a **fix** on an existing feature? → Work on the feature branch, append to changelog.
3. Is this a **chore** (docs, config, refactor with no behavior change)? → Can go directly on `main` with `chore:` commit.

## Branch Strategy

```
main ← feature/HT-XXXXXX
        ↑
        └─ feat/<short-description>  (pre-ticket exploratory work)
```

- **main**: Production-ready. All PRs merge here.
- **feature/HT-XXXXXX**: Ticket-based branches. One branch per ticket.
- **feat/<name>**: Legacy/pre-ticket feature branches (avoid for new work).

## Feature Lifecycle

```
1. Idea → Ticket ID (HT-XXXXXX)
2. Branch: git checkout -b feature/HT-XXXXXX main
3. Work: implement the feature
4. Changelog: create changelogs/HT-XXXXXX.md
5. Registry: update features/feature-registry.json
6. Commit: follow commit convention
7. Merge: PR to main when stable
8. Cleanup: delete checkpoint on success, archive branch
```

## Commit Convention

```
<type>: <imperative description>

Co-Authored-By: Claude <noreply@anthropic.com>
```

Types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`

Examples:
- `feat: add LangSmith dual-tracing for LangGraph node/edge topology`
- `fix: scope figma variables outside try block`
- `chore: update versioning-expert agent with feature lifecycle rules`
- `docs: add LangSmith setup instructions to CLAUDE.md`

## Changelog Format

Per-ticket changelogs at `changelogs/HT-XXXXXX.md`:

```markdown
# HT-XXXXXX — <title>

- **Scenario**: feature | fix | chore
- **Agents**: <agents involved>
- **Branch**: feature/HT-XXXXXX
- **Created**: YYYY-MM-DD HH:MM UTC
- **Status**: in_progress | completed | blocked

## Summary
Brief description of what was done and why.

## Changes
- **file_or_module**: what changed and why
- **file_or_module**: what changed and why

## Commits
- <hash> <message>
```

Existing changelogs are in `changelogs/HT-*.md`.

## Feature Registry

`features/feature-registry.json` tracks every ticket:

```json
{
  "HT-XXXXXX": {
    "title": "...",
    "scenario": "feature",
    "status": "in_progress",
    "created": "2026-06-24T...",
    "agents_involved": ["..."],
    "human_approvals": [],
    "branch": "feature/HT-XXXXXX",
    "changelog_ref": "changelogs/HT-XXXXXX.md",
    "last_updated": "2026-06-24T..."
  }
}
```

Updated via `core.registry` — agents use `create_ticket()` / `update_ticket()`.

## Pre-Code Checklist

Before writing a single line of new code, the versioning expert must be consulted. This means:

1. **Read `.claude/agents/versioning.md`** (this file) — confirm ticket ID, branch, and changelog exist
2. **Read `CLAUDE.md`** — confirm the change aligns with architecture and conventions
3. **Check `features/feature-registry.json`** — is this ticket already tracked?
4. **Check `changelogs/HT-*.md`** — is there an existing changelog for this ticket?
5. **Check `git status`** — are there uncommitted changes that should be committed first?

If creating a new feature from scratch:
- Generate a ticket ID: `HT-{8 hex chars uppercase}`
- Create the branch: `feature/HT-XXXXXX`
- Create the changelog: `changelogs/HT-XXXXXX.md`
- Register in `features/feature-registry.json` via `core.registry.create_ticket()`

## Current State

- **Branch**: `main`
- **Last commits**:
  - `cc8c190` feat: UX/UI generates Figma MCP design spec for screen creation
  - `d9cceb4` fix: scope figma variables outside try block, always write to design_brief
  - `7b10b85` feat: Figma API integration — pull design tokens and assets

## Related Memory

- [[project_context]] — Sprint status and agent inventory
- [[session-handoff-2026-06-22]] — Phase 5: checkpoint/resume, Slack commands
