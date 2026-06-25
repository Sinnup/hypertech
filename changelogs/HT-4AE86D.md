# HT-4AE86D — Dispatcher-readable state file

- **Scenario**: feature
- **Agents**: (infra/observability — no pipeline agent)
- **Branch**: feature/HT-4AE86D
- **Created**: 2026-06-25 18:30 UTC
- **Status**: in_progress

## Summary
The user operates remotely via the Claude app's Dispatcher, which reads this
project's local folder. Added a single canonical state file plus a rendered
markdown dashboard so a remote dispatch can report project progress without
touching Slack, checkpoints, or the registry.

Two concerns are tracked:
- **Live pipeline status** — updated per node as a pipeline runs.
- **Dev work progress** — each stabilization workstream's status (pending →
  in_progress → tested → done / blocked), so the Dispatcher can answer
  "what has Claude finished and tested?" from mobile.

## Changes
- **core/state/state_manager.py** (new): atomic writers (FileLock + `.tmp`→rename,
  same pattern as `core/checkpoint/manager.py`) for `.hypertech/state.json`
  (canonical) and `STATE.md` (rendered dashboard). Public API:
  `update_pipeline_status(state)`, `update_work_progress(...)`, `load()`.
- **core/events/graph_events.py**: hook `update_pipeline_status()` into
  `stream_pipeline()` per-node and at completion (best-effort, never raises).
- **.gitignore**: ignore runtime artifacts `.hypertech/state.json`,
  `.hypertech/state.lock`, `.hypertech/state.tmp`, and root `STATE.md`
  (regenerated each run, read locally by the Dispatcher).

## Commits
- (pending)
