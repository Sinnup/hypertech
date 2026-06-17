# HyperTech — Branch & Commit Convention

Every piece of work that Claude (or a human) does in this repo must go through a feature branch.
**Never commit directly to `main`.**

---

## Branch Naming

```
<type>/HT-<id>-<short-description>
```

| Type        | When to use                                      | Example                                  |
|-------------|--------------------------------------------------|------------------------------------------|
| `feat`      | New agent, new capability, new dashboard section | `feat/HT-009-security-agent`             |
| `fix`       | Bug fix, regression, broken behaviour            | `fix/HT-010-langfuse-span-propagation`   |
| `chore`     | Deps, config, tooling, no prod behaviour change  | `chore/HT-011-bump-langchain`            |
| `refactor`  | Code restructure, no new behaviour               | `refactor/HT-012-workflow-graph-cleanup` |
| `docs`      | Documentation only                               | `docs/HT-013-agent-readme`               |
| `infra`     | Docker, CI/CD, cloud config                      | `infra/HT-014-clickhouse-retention`      |

The `HT-<id>` part is the ticket/prompt ID from `.hypertech/prompt_log.json`.
Use the next sequential number when Claude creates a branch.

---

## Workflow (what Claude does on every task)

```
1. Pull latest main
      git checkout main && git pull

2. Create feature branch
      git checkout -b feat/HT-009-<description>

3. Do the work, commit atomically
      git add <files>
      git commit -m "feat: <short description>"

4. Append entry to .hypertech/prompt_log.json
      { "id": "ht-009", "sha": "<commit sha>", "branch": "feat/HT-009-...", ... }

5. Push branch
      git push -u origin feat/HT-009-<description>

6. Open PR → main (title matches commit message)
```

---

## Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add security agent with OWASP checks
fix: langfuse span context not propagated to child agents
chore: upgrade langchain to 0.3.x
refactor: extract intent classifier into standalone module
docs: add agent architecture diagram
infra: add ClickHouse retention policy to docker-compose
```

---

## prompt_log.json — required fields per entry

When Claude creates a branch, it must add an entry **before** the first commit:

```json
{
  "id": "ht-009",
  "date": "2026-06-16",
  "sha": "<filled after commit>",
  "branch": "feat/HT-009-security-agent",
  "category": "feature",
  "prompt_summary": "The exact natural-language request that triggered this work",
  "key_files": ["agents/security/agent.py"],
  "session_title": "<Cowork session name>",
  "milestone": "<sprint milestone if applicable>"
}
```

---

## PR Rules

- PRs require at least one passing CI check (`.github/workflows/ci.yml`)
- PR title = commit message of the squash commit
- Squash-merge to keep `main` linear
- Delete branch after merge

---

## Current Branch Sequence

| ID     | Branch                                    | Status   |
|--------|-------------------------------------------|----------|
| HT-001 | *(direct to main — pre-convention)*       | merged   |
| HT-002 | *(direct to main — pre-convention)*       | merged   |
| HT-003 | *(direct to main — pre-convention)*       | merged   |
| HT-004 | *(direct to main — pre-convention)*       | merged   |
| HT-005 | *(direct to main — pre-convention)*       | merged   |
| HT-006 | *(direct to main — pre-convention)*       | merged   |
| HT-007 | *(direct to main — pre-convention)*       | merged   |
| HT-008 | *(direct to main — pre-convention)*       | merged   |
| HT-009 | `chore/HT-009-prompt-log`                 | merged   |
| HT-010 | `feat/HT-010-team-metrics-dashboard`      | open     |
| HT-009 | `feat/HT-009-deepseek-ai-provider`        | open     |
