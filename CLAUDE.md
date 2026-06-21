# Hypertech — Agentic SDLC Pipeline

AI-driven software delivery pipeline: prompt → requirements → design → code → deploy.
POC validated. **Phase 1 foundation complete** — building Phase 2 (specialized agents) next.

> **🔄 ACTIVE HANDOFF:** Read `docs/session-handoff-2026-06-20.md` FIRST.
> All 10 pending questions answered (Section 7). Deadline: Monday June 23, 2026 at 4 PM.
> **Demo:** Android TPV app — simulate card payment with button press, APK delivered locally.
> Memory: [[session-handoff-2026-06-20]] · [[project_context]]

## Quick Reference

- **Entry**: `python main.py "your prompt"` or `python main.py --ticket HT-XXX "prompt"`
- **Slack**: `python -m core.notifications.slack_commands`
- **Langfuse**: http://localhost:3000 (see [[langfuse-v4-tracing]] for API gotchas)
- **ChromaDB**: http://localhost:8000
- **Viz Dashboard**: http://localhost:8080/viz (included in Slack command server; see `docs/viz-setup.md`)
- **ngrok domain**: `unplug-active-observing.ngrok-free.dev` (set via `NGROK_DOMAIN` in `.env`)
- **Slack interactive**: `/slack/interactive` endpoint handles approval button clicks

## Architecture (Phase 1)

```
main.py
  └─ LangGraph workflow (core/graph/workflow.py) — dynamic, agent-plan-driven
       │
       ├─ orchestrator (FAST)       — intent → scenario → dynamic agent_plan
       │                                  demo mode: asks clarifying questions via Slack
       ├─ validation_gate           — checks confidence after every agent
       │     ├─ ≥ 60% → context_packer
       │     └─ < 60% or failed → human_escalation → END
       ├─ context_packer (FAST)     — compresses agent output → bullet points
       │     visible graph node — "all steps must be visible"
       │
       ├─ coder (BALANCED)          — HTML/POC code + GitHub (circuit breaker protected)
       ├─ infra (no LLM)            — HTTP server + ngrok
       ├─ ba_compliance (BALANCED)  — requirements + ChromaDB RAG (circuit breaker protected)
       ├─ ux_ui (BALANCED)          — design brief + wireframe
       ├─ architect (POWERFUL)      — HLD + Slack HitL approval
       ├─ pr_review (FAST)          — code style + architecture review
       └─ security (BALANCED)       — Semgrep + LLM review + Slack alerts
```

**Dynamic routing:** orchestrator builds `agent_plan` → every agent → validation_gate → context_packer → next agent in plan. Legacy `next_agent` routing preserved for backward compatibility.

## New Phase 1 Components

| Component | Location | Purpose |
|-----------|----------|---------|
| Agent Registry | `core/agent_registry/` | `@register()` decorator, auto-discovery, AgentOutput Pydantic model |
| Validation Gate | `core/agent_registry/validation_gate.py` | Confidence check after every agent (< 60% → human escalation) |
| Context Packer | `agents/context_packer/agent.py` | Compresses agent outputs into structured bullet points |
| Circuit Breaker | `core/circuit_breaker/` | `@circuit_breaker` decorator — timeout, fallback, Slack approval |
| Prompt Registry | `core/prompt_registry/` | Version-controlled prompts in `prompts/{agent}/system.txt` |
| Enhanced State | `core/state/pipeline_state.py` | `agent_plan`, `agent_outputs`, `context_summaries`, confidence tracking |
| Slack Interactive | `/slack/interactive` endpoint | Receives approval button clicks, unblocks circuit breakers |

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
