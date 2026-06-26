# HT-08AFDC — Shared approval store (Slack approvals never resumed the pipeline)

- **Scenario**: fix
- **Agents**: validation_gate, circuit_breaker (consumers)
- **Branch**: feature/HT-08AFDC
- **Created**: 2026-06-26 02:05 UTC
- **Status**: in_progress

## Summary
Clicking **Approve** in Slack stored the decision but the pipeline never
resumed — the run sat escalated. Root cause: the Slack server runs as
`python -m core.notifications.slack_commands`, which loads that file as the
`__main__` module. The interactive handler stored approvals in
`__main__._approval_results`, but the validation gate does
`from core.notifications.slack_commands import get_approval_result`, which
imports the file *again* under its real dotted name — a **separate module object
with a separate dict**. So approvals stored by Slack were invisible to the gate.

This affected every Slack approval and `/answer --resume`. It was masked in the
automated tests because those import the module by its real name (not `__main__`).

## Changes
- **core/notifications/approval_store.py** (new): holds the single shared
  `_approval_events` / `_approval_results` dicts plus `register_approval_event`,
  `get_approval_result`, and a new `store_approval()` helper. Independent of the
  entrypoint, so there is exactly one store.
- **core/notifications/slack_commands.py**: import the store symbols from
  `approval_store` (re-exported for backward compat) instead of defining them.
- **core/agent_registry/validation_gate.py** & **core/circuit_breaker/decorator.py**:
  import `get_approval_result` / `register_approval_event` from `approval_store`
  directly, so they never resolve through `__main__`.
- **tests/e2e/test_approval_store_shared.py** (new): assert all consumers share
  one store, and that the gate reads what the Slack handler stored.

## Verification
- `uv run pytest tests/e2e/` → 25 passed.
- **Live, via the real `-m` server**: `/new` → `ba_compliance` escalates →
  signed Slack **Approve** → status flips `esc=True → esc=False`, pipeline
  advances past the compliance gate (RAG ran against the real KB) into `ux_ui`.
  Before this fix the same flow stayed stuck at `escalation`.

## Commits
- (pending)
