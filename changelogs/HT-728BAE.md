# HT-728BAE — Predictable coder routing (whole-word keyword match)

- **Scenario**: fix
- **Agents**: orchestrator
- **Branch**: feature/HT-728BAE (stacked on feature/HT-C871A9)
- **Created**: 2026-06-25 20:45 UTC
- **Status**: in_progress

## Summary
A FastAPI backend prompt was routed to `coder_mobile` because `_pick_coder` did
naive substring matching: the mobile keyword **"pos"** matched inside **"POST"**.
The same flaw would mis-route "ios" (matches "scenarios"), "spa" (matches
"space"), etc. Routing is now whole-word, making coder selection predictable.

## Changes
- **agents/orchestrator/agent.py**: new `_kw_match()` helper using word
  boundaries (`\bkw\b`); `_pick_coder()` uses it for mobile/backend/web keyword
  sets instead of substring `in`.
- **tests/e2e/test_coder_routing.py** (new): parametrised routing assertions,
  incl. the POST→not-mobile regression.

## Verification
- `uv run pytest tests/e2e/test_coder_routing.py` → 6 passed.
- Real run: `run_scenarios.py backend` now routes
  `orchestrator → coder_backend → infra`; the generated FastAPI app boots and
  responds: `GET /health` → 200 `{"status":"healthy"}`, `POST /pay` → 200
  `{"status":"approved"}`.

## Commits
- (pending)
