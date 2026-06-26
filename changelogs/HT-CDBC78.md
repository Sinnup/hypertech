# HT-CDBC78 — /viz dashboard shows the whole graph (scrollable)

- **Scenario**: fix
- **Agents**: (viz dashboard frontend)
- **Branch**: feature/HT-CDBC78
- **Created**: 2026-06-26 03:55 UTC
- **Status**: completed

## Summary
The /viz pipeline graph was truncated: the SVG had a fixed
`viewBox="0 0 900 780"` inside an `overflow:hidden` centered panel, but the
full agent DAG spans y≈50–1510. Everything below y=780 (infra, human_escalation,
lower agents) was clipped with no way to scroll.

## Changes
- **static/viz.html**:
  - `.graph-panel` is now `overflow:auto` (scrollable) and centers the SVG.
  - New `fitCanvas(data)` sizes the SVG `viewBox` + width/height to the actual
    node bounds (1 unit = 1px) after render, so the whole DAG is drawn at a
    readable scale and the panel scrolls when it overflows.

## Verification
- Served `/viz` includes `fitCanvas`; `/viz/graph` reports 19 nodes spanning
  y 50–1510 — now fully rendered (was clipped at 780).
- Frontend-only; hard-refresh the /viz tab to pick it up.

## Commits
- (pending)
