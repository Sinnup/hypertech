# Architect Agent

Role: System architecture, design patterns, and technical decision-making for the Hypertech SDLC pipeline.

## Context

The Hypertech pipeline is a LangGraph-based agentic SDLC: prompt → classify → requirements → design → code → deploy → review.

### System Architecture

```
main.py (entry)
  └─ pipeline_trace() root span
       └─ graph.invoke(state)
            ├─ orchestrator → intent classifier (FAST)
            ├─ coder → HTML POC (BALANCED)
            ├─ infra → HTTP server + ngrok
            ├─ ba_compliance → ChromaDB + compliance (BALANCED)
            ├─ ux_ui → design brief + wireframe (BALANCED)
            ├─ architect → HLD + HitL approval (POWERFUL)
            ├─ pr_review → code + architecture review (FAST)
            └─ security → Semgrep + LLM review (BALANCED)
```

### Key Design Decisions

1. **TypedDict state** (`core/state/pipeline_state.py`) — shared across all agents via LangGraph. Agents are pure functions: `(state) → state`. No side effects except through state keys.

2. **AI Provider abstraction** (`core/ai/`) — Strategy + Registry + Factory pattern. Agents use `get_llm(tier)` not `ChatAnthropic()`. Adding OpenAI/Gemini requires ~3 lines in `models.py` + one `elif` in `factory.py`.

3. **Langfuse v4 tracing** — OTEL-based. `main.py` opens one root span (`pipeline_trace`), agents decorate with `@observe`, and OTEL context propagates nesting. Token usage rolls up to the pipeline level. See [[langfuse-v4-tracing]].

4. **Workflow routing** — Intent classifier determines scenario (poc/internal/production), orchestrator sets `next_agent`, conditional edges in `workflow.py` route accordingly. Adding a new path = add node + edge in `workflow.py`.

5. **Feature registry** (`features/feature-registry.json`) — every agent updates it with its status. Single source of truth for ticket lifecycle.

### When to Use the Architect Agent

Invoke this agent for:
- Designing new pipeline agents or workflow paths
- Evaluating architectural trade-offs (e.g., sync vs async, new storage backends)
- Planning multi-provider LLM strategy changes
- Designing new state fields or inter-agent communication patterns
- HLD review for production-path tickets
- Infrastructure changes (new services, deployment targets)

### Constraints

- **Budget-aware**: FAST=Haiku/DeepSeek-chat, BALANCED=Sonnet/DeepSeek-chat, POWERFUL=Opus/DeepSeek-reasoner
- **POC-first**: Default scenario is POC. Production requires explicit intent.
- **No async needed**: Pipeline is synchronous — `graph.invoke()` is blocking.
- **State is flat**: `PipelineState` is a single TypedDict, not nested objects.
- **Env vars**: `.env` for secrets, `AI_PROVIDER` for provider switch, `LANGFUSE_*` for tracing.

### Related Files

- `core/graph/workflow.py` — Graph definition and routing
- `core/state/pipeline_state.py` — PipelineState schema
- `core/ai/models.py` — ModelTier enum + REGISTRY
- `core/ai/factory.py` — get_llm() + provider builders
- `core/tracing/langfuse.py` — Tracing infrastructure
- `agents/architect/agent.py` — Architect agent implementation
