# Hypertech — Agentic SDLC Pipeline

AI-driven software delivery pipeline: prompt → requirements → design → code → deploy.
POC targeting a CTO demo for a Mexican fintech company.

## Quick Reference

- **Entry**: `python main.py "your prompt"` or `python main.py --ticket HT-XXX "prompt"`
- **Slack**: `python -m core.notifications.slack_commands`
- **Langfuse**: http://localhost:3000 (see [[langfuse-v4-tracing]] for API gotchas)
- **ChromaDB**: http://localhost:8000

## Architecture

```
main.py
  └─ LangGraph workflow (core/graph/workflow.py)
       ├─ orchestrator (FAST)       — intent → scenario → routing
       ├─ coder (BALANCED)          — HTML POC generation + GitHub
       ├─ infra (no LLM)            — HTTP server + ngrok
       ├─ ba_compliance (BALANCED)  — requirements + ChromaDB
       ├─ ux_ui (BALANCED)          — design brief + wireframe
       ├─ architect (POWERFUL)      — HLD + Slack HitL approval
       ├─ pr_review (FAST)          — code style + architecture review
       └─ security (BALANCED)       — Semgrep + LLM review + Slack alerts
```

**3 workflow paths:** POC (`orchestrator→coder→infra`), Internal (`orchestrator→ba_compliance→ux_ui`), Production (full chain).

## AI Providers

Set `AI_PROVIDER=claude` (default) or `AI_PROVIDER=deepseek` in `.env`.

| Tier | Claude | DeepSeek |
|------|--------|----------|
| FAST | claude-haiku-4-5 | deepseek-chat |
| BALANCED | claude-sonnet-4-6 | deepseek-chat |
| POWERFUL | claude-opus-4-8 | deepseek-reasoner |

All agents use `get_llm(tier)` from `core/ai/factory.py` — never hardcode a model.
See [[ai-provider-abstraction]] for adding new providers.

## Infrastructure

```bash
docker compose up -d    # ChromaDB + Langfuse (Postgres + ClickHouse + MinIO)
```

- `.env` at project root for secrets (ANTHROPIC_API_KEY, DEEPSEEK_API_KEY, LANGFUSE_*)
- `core/secrets/loader.py` reads `.env` via python-dotenv

## Key Conventions

- **Q&A mode**: When the user asks a question, ONLY answer — no file creation, no implementation, no side effects. Wait for "proceed", "go ahead", "do it", or similar before acting.
- **State**: TypedDict `PipelineState` in `core/state/pipeline_state.py` — all agents read/write it
- **Tracing**: v4.8.1 OTEL-based — `@observe` on agents, `pipeline_trace()` root span in `main.py`
- **Parsing**: `core/ai/parsing.py` handles ``` fences and `<think>` blocks
- **Registry**: `features/feature-registry.json` tracks every ticket's lifecycle
- **Changelogs**: `changelogs/HT-XXXXXX.md` per ticket

## Available Skills

- **langfuse** (`~/.claude/skills/langfuse/skills/`) — Query/manage Langfuse traces, prompts, datasets
- **deepseek** (`~/.claude/skills/deepseek/`) — DeepSeek agent patterns and best practices
- **anthropic** (`~/.claude/skills/anthropic/skills/`) — Claude API patterns, web artifacts builder
- `.claude/skills.md` has detailed when-to-use guidance

## Agent Specialists

- `.claude/agents/architect.md` — Architecture design + system patterns
- `.claude/agents/developer.md` — Coding standards + conventions
- `.claude/agents/qa.md` — Testing + quality gates
- `.claude/agents/versioning.md` — Branching + changelog + release

## Memory

Project memories live in `~/.claude/projects/-Users-sinue-Documents-hypertech/memory/`.
Index: [[project_context]] · [[langfuse-v4-tracing]] · [[ai-provider-abstraction]]
