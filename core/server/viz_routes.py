"""
Viz dashboard + SSE routes as a Flask Blueprint.

Mount this on any Flask app to get the real-time pipeline visualization.
Also exposes ``POST /viz/ingest`` so external (cross-process) pipeline runs
can push events into the SSE stream.

Usage
-----
    from flask import Flask
    from core.server.viz_routes import viz_bp

    app = Flask(__name__)
    app.register_blueprint(viz_bp)
"""

import json
import logging
from pathlib import Path
from queue import Empty

from flask import Blueprint, request, Response, send_file

from core.events.event_bus import event_bus
from core.events.graph_events import get_graph_structure

log = logging.getLogger("werkzeug")
log.setLevel(logging.WARNING)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_STATIC = _PROJECT_ROOT / "static"

viz_bp = Blueprint("viz", __name__)

# Set to deduplicate events in same-process scenarios (event_bus + HTTP ingest)
_seen_events: set[str] = set()
_SEEN_MAX = 500  # prevent unbounded growth


def _is_duplicate(event_type: str, data: dict) -> bool:
    """Return True if this event was already seen (same-process double-publish)."""
    key = f"{event_type}|{data.get('ticket_id','')}|{data.get('agent','')}|{data.get('timestamp','')}"
    if key in _seen_events:
        return True
    _seen_events.add(key)
    if len(_seen_events) > _SEEN_MAX:
        _seen_events.clear()
    return False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@viz_bp.route("/viz")
def serve_dashboard():
    """Serve the single-page dashboard."""
    return send_file(str(_STATIC / "viz.html"))


@viz_bp.route("/viz/graph")
def serve_graph():
    """Return the hard-coded graph structure as JSON."""
    return Response(
        json.dumps(get_graph_structure(), indent=2),
        mimetype="application/json",
    )


@viz_bp.route("/viz/events")
def serve_events():
    """SSE endpoint — streams pipeline events for a ticket."""
    ticket_id = request.args.get("ticket_id", "")
    if not ticket_id:
        return Response("missing ?ticket_id= parameter", status=400)

    def _stream():
        queue = event_bus.subscribe(ticket_id)
        try:
            yield _sse("connected", {"ticket_id": ticket_id})
            while True:
                try:
                    event = queue.get(timeout=30)
                except Empty:
                    yield ": keepalive\n\n"
                    continue
                yield _sse(event["type"], event["data"])
        except GeneratorExit:
            pass
        finally:
            event_bus.unsubscribe(ticket_id, queue)

    return Response(_stream(), mimetype="text/event-stream")


@viz_bp.route("/viz/status")
def serve_status():
    """Lightweight status check."""
    ticket_id = request.args.get("ticket_id", "")
    if not ticket_id:
        return Response("missing ?ticket_id= parameter", status=400)
    with event_bus._lock:
        subs = len(event_bus._subscribers.get(ticket_id, []))
    return Response(
        json.dumps({"ticket_id": ticket_id, "subscribers": subs}),
        mimetype="application/json",
    )


@viz_bp.route("/viz/stop", methods=["POST"])
def stop_run():
    """Request a graceful stop of the run for *ticket_id* (button in /viz)."""
    ticket_id = request.args.get("ticket_id", "") or (request.get_json(silent=True) or {}).get("ticket_id", "")
    if not ticket_id:
        return Response("missing ticket_id", status=400)
    from core.control.cancel import request_stop
    request_stop(ticket_id)
    event_bus.publish(ticket_id, "stop_requested", {"ticket_id": ticket_id})
    return Response(json.dumps({"ok": True, "ticket_id": ticket_id}), mimetype="application/json")


@viz_bp.route("/viz/ingest", methods=["POST"])
def ingest_event():
    """
    Receive a pipeline event from an external process and fan it out to SSE
    subscribers.  Idempotent — duplicate events are silently dropped.

    This is the bridge for cross-process pipelines (e.g. CLI ``python main.py``
    talking to a standalone viz server or the Slack command server).
    """
    payload = request.get_json(silent=True) or {}
    event_type = payload.get("type", "")
    ticket_id = (payload.get("data") or {}).get("ticket_id", "")
    data = payload.get("data", {})

    if not ticket_id:
        return Response("missing ticket_id in payload", status=400)

    if _is_duplicate(event_type, data):
        return Response("ok (duplicate)", status=200)

    event_bus.publish(ticket_id, event_type, data)

    # Broadcast run/command announcements to the lobby so a no-ticket dashboard
    # can auto-discover and switch to them (pipelines + commands like /reload-kb).
    if event_type in ("pipeline_start", "plan_ready"):
        event_bus.publish("__lobby__", event_type, data)

    return Response("ok", status=200)


# ---------------------------------------------------------------------------
# Lobby — auto-discovery for dashboards without a ticket_id
# ---------------------------------------------------------------------------

@viz_bp.route("/viz/events/lobby")
def serve_lobby():
    """
    SSE endpoint that broadcasts ``pipeline_start`` events for ALL tickets.
    Dashboards connect here when no ``?ticket_id=`` is specified, then switch
    to the ticket-specific stream when a pipeline is discovered.
    """
    ticket_id = "__lobby__"

    def _stream():
        queue = event_bus.subscribe(ticket_id)
        try:
            yield _sse("connected", {"lobby": True})
            while True:
                try:
                    event = queue.get(timeout=30)
                except Empty:
                    yield ": keepalive\n\n"
                    continue
                yield _sse(event["type"], event["data"])
        except GeneratorExit:
            pass
        finally:
            event_bus.unsubscribe(ticket_id, queue)

    return Response(_stream(), mimetype="text/event-stream")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sse(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
