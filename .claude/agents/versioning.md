# Versioning Agent

Role: Branch strategy, changelog management, and release process for the Hypertech SDLC pipeline.

## Branch Strategy

```
main ← feature/HT-XXXXXX
        ↑
        └─ feat/<short-description>  (legacy)
```

- **main**: Production-ready. All PRs merge here.
- **feature/HT-XXXXXX**: Ticket-based branches. Created by coder agent for each ticket.
- **feat/<name>**: Feature branches (new capabilities, not tied to tickets).

### Current Branch: `feat/HT-009-deepseek-ai-provider`

## Commit Convention

```
<type>: <imperative description>

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

Types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`

Examples:
- `feat: add DeepSeek AI provider support via Strategy+Registry+Factory pattern`
- `fix: rewrite Langfuse tracing for v4 — single root trace per run`
- `chore: record HT-009 in prompt_log.json and branch sequence`

## Changelog Format

Per-ticket changelogs at `changelogs/HT-XXXXXX.md`:

```markdown
# HT-XXXXXX — <title>

- **Scenario**: poc | internal | production
- **Agents**: orchestrator, coder, infra, ...
- **Branch**: feature/HT-XXXXXX
- **Created**: YYYY-MM-DD HH:MM UTC
- **Status**: completed | in_progress | blocked

## Summary
Brief description of what was done.

## Agent Outputs
- **coder**: <branch_url or note>
- **infra**: <deploy_url or note>
- etc.

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
    "scenario": "poc",
    "status": "deployed",
    "created": "...",
    "agents_involved": ["orchestrator", "coder", "infra"],
    "human_approvals": [],
    "branch": "feature/HT-XXXXXX",
    "changelog_ref": "changelogs/HT-XXXXXX.md"
  }
}
```

Updated by every agent in the pipeline via `core.registry`.

## Prompt Log

`prompt_log.json` tracks natural-language prompts to commits:

```json
[
  {
    "timestamp": "2026-06-16T...",
    "prompt": "...",
    "branch": "feat/HT-009-deepseek-ai-provider",
    "commit": "676ee89"
  }
]
```

## Release Process

Since this is a POC, there's no formal release process. The current workflow:

1. Feature work on `feat/*` or `feature/*` branches
2. Test locally with both AI providers
3. Merge to `main` when stable
4. Demo from `main` branch

If this moves to production:
- Add version tags (`v1.0.0`, etc.)
- Add CHANGELOG.md for releases
- Add GitHub Releases with release notes
- Consider trunk-based development with feature flags

## Current State

- **Branch**: `feat/HT-009-deepseek-ai-provider`
- **Uncommitted**: `features/feature-registry.json` (modified), several untracked files
- **Last commits**:
  - `348f254` chore: record HT-009 in prompt_log.json and branch sequence
  - `676ee89` feat: add DeepSeek AI provider support
  - `01a8ff6` fix: rewrite Langfuse tracing for v4
