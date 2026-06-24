# Hypertech — Agentic SDLC Pipeline

AI-driven software delivery pipeline: prompt → requirements → design → code → test → deploy.
**Phases 1–4 complete. 17 agents registered.**

> **Demo:** `python main.py "Build an Android TPV app. Button press simulates card insertion → payment processed. APK delivered locally."`
> Memory: [[session-handoff-2026-06-21]] · [[project_context]]

## Quick Reference

- **Entry**: `python main.py "your prompt"` or `python main.py --ticket HT-XXX "prompt"`
- **Resume**: `python main.py --resume` (picks most recent checkpoint) or `python main.py --resume --ticket HT-XXX`
- **Slack**: `python -m core.notifications.slack_commands` (7 commands — see Slack Commands below)
- **Langfuse**: http://localhost:3000 (see [[langfuse-v4-tracing]] for API gotchas)
- **ChromaDB**: http://localhost:8000
- **Viz Dashboard**: http://localhost:8080/viz (included in Slack command server)
- **ngrok domain**: `unplug-active-observing.ngrok-free.dev` (set via `NGROK_DOMAIN` in `.env`)
- **Slack interactive**: `/slack/interactive` endpoint handles approval button clicks

## Slack Commands (primary UI)

| Command | Usage | Description |
|---------|-------|-------------|
| `/new` | `/new [--scenario poc\|internal\|production] <prompt>` | Start a new pipeline |
| `/resume` | `/resume [ticket_id]` | Resume from checkpoint (picks most recent if no ID) |
| `/checkpoints` | `/checkpoints` | List all saved checkpoints (newest first, max 10) |
| `/status` | `/status HT-XXXXXX` | Ticket details + checkpoint availability + resume hint |
| `/reload-kb` | `/reload-kb` | Reload Google Drive knowledge base into ChromaDB |
| `/deploy` | `/deploy HT-XXXXXX [local\|cert\|prod]` | Deploy a completed ticket |
| `/switch-provider` | `/switch-provider claude\|deepseek` | Switch AI provider at runtime |

## Architecture (Phases 1–4)

```
main.py
  └─ LangGraph workflow (core/graph/workflow.py) — dynamic, agent-plan-driven
       │
       ├─ orchestrator (FAST)         — intent → scenario → dynamic agent_plan
       │                                   keyword routing: mobile/web/backend/generic
       │                                   demo mode: Slack clarifying questions
       │                                   resume detection: skip if resumed_from_checkpoint
       ├─ validation_gate             — confidence check after every agent
       │     ├─ ≥ 60% → context_packer
       │     └─ < 60% or failed → human_escalation → END
       │     💾 checkpoint saved here (after pass, before index advance)
       ├─ context_packer (FAST)       — compresses output → bullet points (visible node)
       │     advances agent_plan_index for next agent
       │
       ├─ [Analysis / Design — right column]
       │   ├─ ba_compliance (BALANCED) — ChromaDB RAG, EMV/CNBV/LACP compliance check
       │   ├─ ux_ui (BALANCED)         — design brief JSON + HTML wireframe
       │   ├─ architect (POWERFUL)     — HLD JSON + Mermaid diagram + Slack HitL approval
       │   └─ design_synthesizer (BALANCED) — merges BA+Architect+UX/UI → unified brief
       │
       ├─ [Code Generation — left column]
       │   ├─ coder_mobile (BALANCED) — Android Kotlin/Compose + TPV demo template
       │   ├─ coder_web (BALANCED)    — HTML+Tailwind+JS prototype
       │   ├─ coder_backend (BALANCED)— FastAPI/Python project
       │   └─ coder (BALANCED)        — generic HTML coder (fallback)
       │
       ├─ [Quality Gates — right column, 3-iteration loops]
       │   ├─ pr_review (FAST)        — style/arch/bug review; loops → coder up to 3×
       │   └─ security (BALANCED)     — OWASP Top 10 + Semgrep; loops → coder up to 3×
       │
       ├─ [Delivery — left column]
       │   ├─ test_generator (BALANCED) — pytest/Jest/Espresso test files
       │   ├─ devops (BALANCED)         — GitHub Actions CI/CD workflow
       │   └─ infra (FAST)             — HTTP/ngrok (web), build instructions (mobile/backend)
       │
       └─ human_escalation → END
```

## Pipeline Plans (orchestrator)

| Scenario | Plan |
|----------|------|
| `poc` + mobile | orchestrator → coder_mobile → infra |
| `poc` + web | orchestrator → coder_web → infra |
| `poc` + backend | orchestrator → coder_backend → infra |
| `poc` generic | orchestrator → coder → infra |
| `internal` | orchestrator → ba_compliance → ux_ui → design_synthesizer → {coder} → test_generator → infra |
| `production` | orchestrator → ba_compliance → ux_ui → architect → design_synthesizer → {coder} → pr_review → security → test_generator → devops → infra |

## Review Iteration Loops

Both `pr_review` and `security` use **plan-injection** to loop back to the coder:

```
changes_requested + iterations < 3  → inject [coder_X, reviewer] back into agent_plan
blocked / iterations >= 3           → confidence = 0.3 → validation_gate → human_escalation
```

## Phase Components

| Phase | Component | Location | Purpose |
|-------|-----------|----------|---------|
| 1 | Agent Registry | `core/agent_registry/` | `@register()`, auto-discovery, AgentOutput |
| 1 | Validation Gate | `core/agent_registry/validation_gate.py` | Confidence < 60% → human escalation |
| 1 | Context Packer | `agents/context_packer/agent.py` | Compress outputs for next agent |
| 1 | Circuit Breaker | `core/circuit_breaker/` | Timeout + Slack approval before fallback |
| 1 | Prompt Registry | `core/prompt_registry/` | `prompts/{agent}/system.txt` |
| 2 | coder_mobile | `agents/coder_mobile/` | Android Kotlin/Compose; TPV demo template |
| 2 | coder_web | `agents/coder_web/` | HTML+Tailwind+JS |
| 2 | coder_backend | `agents/coder_backend/` | FastAPI/Python |
| 2 | design_synthesizer | `agents/design_synthesizer/` | Merge BA+Architect+UX/UI |
| 2 | Mermaid in architect | `agents/architect/agent.py` | C4 diagram → Slack |
| 3 | test_generator | `agents/test_generator/` | pytest/Jest/Espresso tests |
| 3 | devops | `agents/devops/` | GitHub Actions CI/CD |
| 3 | pr_review (enhanced) | `agents/pr_review/agent.py` | 3-iteration review loop |
| 3 | security (enhanced) | `agents/security/agent.py` | OWASP Top 10 + iteration loop |
| 4 | Dashboard layout | `core/events/graph_events.py` | All 17 agents, updated scenarios |
| 5 | Checkpoint/Resume | `core/checkpoint/manager.py` | FileLock + atomic write; save after validation_gate |
| 5 | Slack /resume + /checkpoints | `core/notifications/slack_commands.py` | Resume from crash, list checkpoints |

## Checkpoint/Resume

Pipeline state is persisted to `.hypertech/checkpoints/{ticket_id}.json` after each
agent passes validation (post-validation_gate, pre-context_packer).  On crash or
interrupt, the pipeline can be resumed:

```bash
python main.py --resume              # most recent checkpoint
python main.py --resume --ticket HT-XXX  # specific ticket
python main.py --ticket HT-XXX "prompt"  # auto-detects existing checkpoint
```

**Save point**: After `validation_gate` passes (before `context_packer` advances the index).
This ensures the saved `agent_plan_index` correctly points to the NEXT agent to run.

**Index semantics** (fixed in Phase 5):
- `agent_plan_index` starts at 0 (orchestrator just completed at plan[0])
- `context_packer` advances: 0→1→2→...
- `_plan_router` uses `plan[agent_plan_index]` to select the next agent

**Cleanup**: Checkpoint file is deleted on successful pipeline completion.
Emergency checkpoint is saved on any `BaseException` before propagating.

**FileLock**: Same pattern as `core/registry.py` — thread/process-safe per ticket.
Atomic writes via `.tmp` → rename (never corrupts on partial write).

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
- **Versioning gate**: Before making ANY new code, read `.claude/agents/versioning.md` — every feature needs a ticket, branch, changelog, and registry entry.
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

- **`.claude/agents/versioning.md`** — **READ FIRST before any code change.** Branch strategy, feature lifecycle, commit conventions, pre-code checklist.
- `.claude/agents/architect.md` — Architecture design + system patterns
- `.claude/agents/developer.md` — Coding standards + conventions
- `.claude/agents/qa.md` — Testing + quality gates

## Memory

Project memories live in `~/.claude/projects/-Users-sinue-Documents-hypertech/memory/`.
Index: [[project_context]] · [[langfuse-v4-tracing]] · [[ai-provider-abstraction]]
