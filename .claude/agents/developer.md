# Developer Agent

Role: Code implementation, conventions, and development workflow for the Hypertech SDLC pipeline.

## Project Structure

```
hypertech/
├── main.py              # Entry point — CLI, pipeline run, tracing
├── core/
│   ├── ai/              # AI provider abstraction (models, factory, parsing)
│   ├── graph/           # LangGraph workflow definition
│   ├── state/           # PipelineState TypedDict
│   ├── tracing/         # Langfuse v4 client setup
│   ├── routing/         # Intent classifier
│   ├── memory/          # ChromaDB client
│   ├── notifications/   # Slack commands + webhooks
│   ├── secrets/         # .env loader
│   └── registry.py      # Feature registry (JSON file store)
├── agents/              # One package per agent
│   ├── orchestrator/    # Entry agent — intent + routing
│   ├── coder/           # HTML POC generator
│   ├── infra/           # HTTP server deployer
│   ├── ba_compliance/   # Requirements + compliance
│   ├── knowledge_base/  # ChromaDB query wrapper
│   ├── ux_ui/           # Design brief + wireframe
│   ├── architect/       # HLD generator
│   ├── pr_review/       # Code review agent
│   ├── security/        # SAST + LLM review
│   ├── qa/              # QA agent
│   └── support/         # Support agent
├── features/            # Feature registry JSON store
├── changelogs/          # Per-ticket changelog files
├── docker/              # ClickHouse config files
├── docker-compose.yml   # ChromaDB + Langfuse services
└── .github/workflows/   # CI/CD pipelines
```

## Coding Conventions

### Imports
```python
# 1. Standard library
import os
from typing import Optional

# 2. Third-party
from langfuse import observe
from langchain_core.language_models import BaseChatModel

# 3. Project modules
from core.state.pipeline_state import PipelineState, agent_message
from core.tracing.langfuse import get_client
from core.ai.factory import get_llm, get_model_id
from core.ai.models import ModelTier
```

### Agent Pattern

Every agent follows this structure:
```python
"""
docstring explaining the agent's role
"""

from langfuse import observe
from core.state.pipeline_state import PipelineState
from core.tracing.langfuse import get_client

@observe(name="agent-name")
def run(state: PipelineState) -> PipelineState:
    """Agent entry point — called by LangGraph."""
    # 1. Extract from state
    # 2. Call LLM (if needed) via get_llm(tier)
    # 3. Record to Langfuse via get_client()
    # 4. Update state + registry
    # 5. Return state
    return state
```

### Langfuse Tracing

- **Root span**: `pipeline_trace()` context manager in `main.py` wraps full run
- **Agent spans**: `@observe(name="...")` decorator on `run()` functions — auto-nests under root
- **LLM generations**: `get_client().update_current_generation(model=..., usage_details={...})` after each LLM call
- **Agent metadata**: `get_client().update_current_span(input=..., output=...)` for non-LLM data
- **Flush**: Always call `flush()` after pipeline completes

### State Updates

- Read from `PipelineState` typed keys only
- Use `agent_message()` helper for inter-agent communication
- Update `features/feature-registry.json` via `core.registry` module
- Set `current_agent`, `next_agent`, `status` on every state transition

### LLM Calling

```python
from core.ai.factory import get_llm, get_model_id
from core.ai.models import ModelTier

llm = get_llm(ModelTier.BALANCED, temperature=0.3)  # or FAST / POWERFUL
response = llm.invoke(messages)
# Record to Langfuse:
client = get_client()
if client:
    client.update_current_generation(
        model=get_model_id(tier),
        output=content,
        usage_details={"input": in_tok, "output": out_tok},
    )
```

### Parsing

`core/ai/parsing.py` provides:
- `parse_json(text)` — strips fences, parses, returns dict or None
- `strip_fences(text)` — removes ``` fences and `<think>` blocks

## Testing

```bash
# Run a POC pipeline
python main.py "Build a payment confirmation screen"

# Run with custom ticket ID
python main.py --ticket HT-TEST01 "Login form prototype"

# Check Langfuse at http://localhost:3000 for traces
```

## Adding a New Agent

1. Create `agents/<name>/agent.py` with `@observe(name="<name>") def run(state):`
2. Add to `core/graph/workflow.py`: import + `graph.add_node()` + edges
3. Add routing in appropriate conditional edge function
4. Update `core/routing/intent_classifier.py` if new scenario
5. Add entry to `CLAUDE.md` architecture diagram

## Related Memory

- [[project_context]] — Sprint status and agent inventory
- [[ai-provider-abstraction]] — How get_llm() works
- [[langfuse-v4-tracing]] — Tracing API dos and don'ts
