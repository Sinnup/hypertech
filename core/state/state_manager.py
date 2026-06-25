"""
State Manager — single dispatcher-readable progress file.

The Claude app's Dispatcher reads this project's *local folder* when asked about
project status.  This module maintains one canonical state file plus a
human/agent-readable markdown dashboard so a remote dispatch can report progress
without touching Slack, checkpoints, or the registry.

Two artifacts, both written atomically (FileLock + ``.tmp`` → rename, the same
pattern as ``core/checkpoint/manager.py``):

  ``.hypertech/state.json``   canonical machine state (schema below)
  ``STATE.md`` (repo root)    rendered dashboard — what the Dispatcher surfaces

``state.json`` schema::

    {
      "version": 1,
      "updated_at": ISO8601,
      "pipeline": {                  # latest/active pipeline run, or null
        "ticket_id", "scenario", "status", "current_agent",
        "completed_agents": [...], "agent_plan": [...], "agent_plan_index",
        "percent_complete", "human_escalation", "can_resume", "deploy_url",
        "updated_at"
      },
      "work": {                      # dev workstream progress (stabilization effort)
        "HT-XXXXXX": {"title", "status", "branch", "notes", "updated_at"}
      }
    }

Two writers:
  * ``update_pipeline_status(state)``  — called per-node by the pipeline stream.
  * ``update_work_progress(...)``      — called as each dev workstream is verified.

All writes are best-effort: failures are logged, never raised into the pipeline.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock

from core.secrets.loader import get_optional

logger = logging.getLogger(__name__)

_STATE_JSON = Path(get_optional("STATE_JSON_PATH", ".hypertech/state.json"))
_STATE_MD = Path(get_optional("STATE_MD_PATH", "STATE.md"))
_LOCK_PATH = _STATE_JSON.with_suffix(".lock")
_VERSION = 1

# Ordered statuses for the work table (also defines the legend in STATE.md).
WORK_STATUSES = ("pending", "in_progress", "tested", "done", "blocked")
_STATUS_EMOJI = {
    "pending": "⏳",
    "in_progress": "🔨",
    "tested": "🧪",
    "done": "✅",
    "blocked": "🚧",
}


def _lock() -> FileLock:
    _STATE_JSON.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(str(_LOCK_PATH), timeout=15)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default() -> dict:
    return {"version": _VERSION, "updated_at": _now(), "pipeline": None, "work": {}}


def _load_unlocked() -> dict:
    if _STATE_JSON.exists():
        try:
            data = json.loads(_STATE_JSON.read_text())
            data.setdefault("work", {})
            data.setdefault("pipeline", None)
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Corrupt state.json (%s) — reinitialising", exc)
    return _default()


def _write_unlocked(data: dict) -> None:
    data["version"] = _VERSION
    data["updated_at"] = _now()

    _STATE_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_JSON.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.rename(_STATE_JSON)  # atomic on POSIX

    md = _render_md(data)
    md_tmp = _STATE_MD.with_suffix(".md.tmp")
    md_tmp.write_text(md)
    md_tmp.rename(_STATE_MD)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def update_pipeline_status(state: dict) -> None:
    """Snapshot the live pipeline run from a PipelineState dict.

    Best-effort: any failure is logged and swallowed so the pipeline never
    breaks because the dashboard could not be written.
    """
    try:
        plan = state.get("agent_plan") or []
        idx = int(state.get("agent_plan_index", 0) or 0)
        completed = list((state.get("agent_outputs") or {}).keys())
        pct = int(round(100 * min(idx, len(plan)) / len(plan))) if plan else 0
        escalated = bool(state.get("human_escalation"))

        pipeline = {
            "ticket_id": state.get("ticket_id"),
            "scenario": state.get("scenario"),
            "status": state.get("status"),
            "current_agent": state.get("current_agent"),
            "completed_agents": completed,
            "agent_plan": plan,
            "agent_plan_index": idx,
            "percent_complete": pct,
            "human_escalation": escalated,
            "can_resume": escalated or bool(state.get("error")),
            "deploy_url": state.get("deploy_url"),
            "updated_at": _now(),
        }
        with _lock():
            data = _load_unlocked()
            data["pipeline"] = pipeline
            _write_unlocked(data)
    except Exception:  # never propagate into the pipeline
        logger.debug("update_pipeline_status failed", exc_info=True)


def update_work_progress(
    ticket_id: str,
    title: str,
    status: str,
    branch: str | None = None,
    notes: str | None = None,
) -> None:
    """Record dev-workstream progress so a remote Dispatcher can report it.

    ``status`` should be one of :data:`WORK_STATUSES`.
    """
    if status not in WORK_STATUSES:
        logger.warning("Unknown work status %r for %s", status, ticket_id)
    try:
        with _lock():
            data = _load_unlocked()
            entry = data["work"].get(ticket_id, {})
            entry.update({
                "title": title,
                "status": status,
                "updated_at": _now(),
            })
            if branch is not None:
                entry["branch"] = branch
            if notes is not None:
                entry["notes"] = notes
            data["work"][ticket_id] = entry
            _write_unlocked(data)
    except Exception:
        logger.debug("update_work_progress failed", exc_info=True)


def load() -> dict:
    """Return the current state dict (for /status, tests, or callers)."""
    with _lock():
        return _load_unlocked()


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def _render_md(data: dict) -> str:
    lines: list[str] = []
    lines.append("# Hypertech — Project State")
    lines.append("")
    lines.append(f"_Auto-generated by `core/state/state_manager.py` — do not edit by hand._")
    lines.append(f"_Last updated: {data.get('updated_at', '')}_")
    lines.append("")

    # -- Work progress -------------------------------------------------------
    lines.append("## Work Progress")
    lines.append("")
    work = data.get("work") or {}
    if not work:
        lines.append("_No workstreams tracked yet._")
    else:
        lines.append("| Ticket | Title | Status | Branch | Notes |")
        lines.append("|--------|-------|--------|--------|-------|")
        for tid, w in work.items():
            emoji = _STATUS_EMOJI.get(w.get("status", ""), "")
            lines.append(
                f"| {tid} | {w.get('title', '')} | {emoji} {w.get('status', '')} "
                f"| {w.get('branch', '')} | {w.get('notes', '') or ''} |"
            )
    lines.append("")
    lines.append(
        "Legend: ⏳ pending · 🔨 in_progress · 🧪 tested · ✅ done · 🚧 blocked"
    )
    lines.append("")

    # -- Latest pipeline run -------------------------------------------------
    lines.append("## Latest Pipeline Run")
    lines.append("")
    p = data.get("pipeline")
    if not p:
        lines.append("_No pipeline run recorded._")
    else:
        lines.append(f"- **Ticket**: {p.get('ticket_id')}")
        lines.append(f"- **Scenario**: {p.get('scenario')}")
        lines.append(f"- **Status**: {p.get('status')}")
        lines.append(f"- **Progress**: {p.get('percent_complete')}% "
                     f"(agent {p.get('agent_plan_index')}/{len(p.get('agent_plan') or [])})")
        lines.append(f"- **Current agent**: {p.get('current_agent')}")
        if p.get("completed_agents"):
            lines.append(f"- **Completed**: {', '.join(p['completed_agents'])}")
        if p.get("agent_plan"):
            lines.append(f"- **Plan**: {' → '.join(p['agent_plan'])}")
        if p.get("human_escalation"):
            lines.append("- **⚠️ Awaiting human approval** (escalated)")
        if p.get("can_resume"):
            lines.append(f"- **Resume**: `python main.py --resume --ticket {p.get('ticket_id')}`")
        if p.get("deploy_url"):
            lines.append(f"- **Deploy URL**: {p.get('deploy_url')}")
        lines.append(f"- **Updated**: {p.get('updated_at')}")
    lines.append("")
    return "\n".join(lines)
