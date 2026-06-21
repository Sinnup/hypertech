"""
Slack slash command handlers — responds to /status, /reload-kb, /deploy, /new.

Run this as a standalone Flask server (or mount on an existing app) and point
your Slack app's slash command Request URL at it.

Usage (local dev):
    python -m core.notifications.slack_commands
    # then use ngrok to expose it: ngrok http 8080
    # set the Slack slash command URL to: https://<ngrok>/slack/commands
"""

import json
import hashlib
import hmac
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread

from flask import Flask, request, jsonify, abort

from core.secrets.loader import get, get_optional
from core.notifications import slack
from core.server.viz_routes import viz_bp
from ingestion.google_drive import ingest as drive_ingest
from core.ai.fallback import override_provider, current_provider

app = Flask(__name__)
app.register_blueprint(viz_bp)

_REGISTRY_PATH = Path(get_optional("FEATURE_REGISTRY_PATH", "features/feature-registry.json"))


def _verify_slack_signature(body: bytes, timestamp: str, signature: str) -> bool:
    """Verify the request came from Slack using the signing secret."""
    signing_secret = get_optional("SLACK_SIGNING_SECRET", "")
    if not signing_secret:
        return True  # skip in local dev if not configured
    if abs(time.time() - int(timestamp)) > 60 * 5:
        return False
    base = f"v0:{timestamp}:{body.decode()}"
    expected = "v0=" + hmac.new(signing_secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@app.before_request
def verify_slack():
    if request.path in ("/slack/commands", "/slack/interactive"):
        ts = request.headers.get("X-Slack-Request-Timestamp", "0")
        sig = request.headers.get("X-Slack-Signature", "")
        if not _verify_slack_signature(request.get_data(), ts, sig):
            abort(403)


@app.route("/slack/commands", methods=["POST"])
def slack_commands():
    command = request.form.get("command", "")
    text = request.form.get("text", "").strip()
    user_id = request.form.get("user_id", "unknown")

    if command == "/status":
        return _handle_status(text)
    if command == "/reload-kb":
        return _handle_reload_kb(user_id)
    if command == "/deploy":
        return _handle_deploy(text, user_id)
    if command == "/switch-provider":
        return _handle_switch_provider(text, user_id)
    if command == "/new":
        return _handle_new(text, user_id)

    return jsonify({"text": f"Unknown command: {command}"}), 200


def _handle_status(ticket_id: str):
    if not ticket_id:
        return jsonify({"text": "Usage: `/status HT-XXXXXX`"}), 200

    registry = {}
    if _REGISTRY_PATH.exists():
        registry = json.loads(_REGISTRY_PATH.read_text())

    entry = registry.get(ticket_id)
    if not entry:
        return jsonify({"text": f"Ticket *{ticket_id}* not found in the registry."}), 200

    agents = ", ".join(entry.get("agents_involved", []))
    lines = [
        f"*Ticket:* {ticket_id}",
        f"*Title:* {entry.get('title', '—')}",
        f"*Scenario:* {entry.get('scenario', '—')}",
        f"*Status:* {entry.get('status', '—')}",
        f"*Agents:* {agents or '—'}",
        f"*Branch:* {entry.get('branch', '—')}",
        f"*Last updated:* {entry.get('last_updated', '—')}",
    ]
    if entry.get("deploy_url"):
        lines.append(f"*Live at:* {entry['deploy_url']}")

    return jsonify({"text": "\n".join(lines)}), 200


def _handle_reload_kb(user_id: str):
    def _run():
        try:
            result = drive_ingest.run()
            slack.status(
                "KB",
                f"✅ KB reload complete — {result['ingested']} chunks ingested "
                f"by <@{user_id}>"
            )
        except Exception as e:
            slack.status("KB", f"❌ KB reload failed: {e}")

    Thread(target=_run, daemon=True).start()
    return jsonify({"text": "🔄 KB reload started — you'll get a Slack notification when done."}), 200


def _handle_deploy(text: str, user_id: str):
    parts = text.split()
    if len(parts) < 1:
        return jsonify({"text": "Usage: `/deploy HT-XXXXXX [local|cert|prod]`"}), 200

    ticket_id = parts[0]
    env = parts[1] if len(parts) > 1 else "local"
    slack.status(ticket_id, f"🚀 Deploy to *{env}* triggered by <@{user_id}>")
    return jsonify({"text": f"Deploy to `{env}` queued for {ticket_id}."}), 200


def _handle_switch_provider(text: str, user_id: str):
    """Switch the active AI provider at runtime. Usage: /switch-provider claude|deepseek"""
    valid = {"claude", "deepseek"}
    target = text.strip().lower()

    if not target:
        current = current_provider()
        return jsonify({
            "text": f"Current provider: `{current}`. "
                    f"Valid options: {', '.join(sorted(valid))}. "
                    f"Usage: `/switch-provider claude`"
        }), 200

    if target not in valid:
        return jsonify({
            "text": f"❌ Unknown provider `{target}`. Valid: {', '.join(sorted(valid))}"
        }), 200

    previous = current_provider()
    override_provider(target)
    slack.alert(
        f"🔀 *Provider Switched*\n"
        f"*From:* `{previous}` → *To:* `{target}`\n"
        f"*By:* <@{user_id}>\n"
        f"*Note:* All subsequent LLM calls in this session will use `{target}`.",
        channel="#pipeline-alerts",
    )
    return jsonify({
        "text": f"✅ Provider switched: `{previous}` → `{target}`. "
                f"All subsequent `/new` runs will use `{target}`."
    }), 200


def _handle_new(text: str, user_id: str):
    """
    Kick off a new pipeline run from Slack.

    Usage: /new [--scenario poc|internal|production] <prompt>

    Responds immediately with a ticket ID, then runs the pipeline
    asynchronously.  The caller gets a Slack notification when the
    pipeline completes (or fails).
    """
    # ── parse ──────────────────────────────────────────────────────────
    scenario_hint: str | None = None
    prompt = text.strip()

    m = re.match(r"^--scenario\s+(poc|internal|production)\s+(.+)", prompt, re.IGNORECASE)
    if m:
        scenario_hint = m.group(1).lower()
        prompt = m.group(2).strip()

    if not prompt:
        return jsonify({
            "text": (
                "Usage: `/new [--scenario poc|internal|production] <prompt>`\n"
                "Examples:\n"
                "• `/new Quick POC: landing page with hero`\n"
                "• `/new --scenario production Build a payment KYC compliance screen`"
            )
        }), 200

    # ── ticket ─────────────────────────────────────────────────────────
    ticket_id = f"HT-{uuid.uuid4().hex[:6].upper()}"

    # Pre-register so /status works immediately
    from core.registry import create_ticket
    now = datetime.now(timezone.utc).isoformat()
    create_ticket(ticket_id, {
        "title": prompt[:80],
        "scenario": scenario_hint or "auto-detect",
        "status": "queued",
        "created": now,
        "agents_involved": [],
        "human_approvals": [],
        "branch": f"feature/{ticket_id}",
        "changelog_ref": f"changelogs/{ticket_id}.md",
    })

    # ── launch (async) ─────────────────────────────────────────────────
    def _run():
        try:
            from main import run_pipeline
            result = run_pipeline(ticket_id, prompt)

            status = result.get("status", "unknown")
            scenario = result.get("scenario", "?")
            deploy_url = result.get("deploy_url")
            coder_out = result.get("coder_output") or {}
            branch_url = coder_out.get("branch_url")
            error = result.get("error")

            if error:
                slack.alert(
                    f"❌ *Pipeline {ticket_id} failed*\n"
                    f"*Prompt:* {prompt[:100]}\n"
                    f"*Scenario:* {scenario}\n"
                    f"*Error:* {error}\n"
                    f"*Triggered by:* <@{user_id}>",
                    channel="#pipeline-alerts",
                )
                return

            lines = [
                f"✅ *Pipeline {ticket_id} complete*",
                f"*Prompt:* {prompt[:100]}",
                f"*Scenario:* {scenario}",
                f"*Status:* {status}",
            ]
            if branch_url:
                lines.append(f"*Branch:* {branch_url}")
            if deploy_url:
                lines.append(f"*Live at:* {deploy_url}")
            lines.append(f"*Triggered by:* <@{user_id}>")

            slack.alert("\n".join(lines), channel="#pipeline-alerts")

        except Exception as e:
            slack.alert(
                f"❌ *Pipeline {ticket_id} crashed*\n"
                f"*Prompt:* {prompt[:100]}\n"
                f"*Error:* {type(e).__name__}: {e}\n"
                f"*Triggered by:* <@{user_id}>",
                channel="#pipeline-alerts",
            )
            # Update registry with failure
            try:
                from core.registry import update_ticket
                update_ticket(ticket_id, {"status": "failed", "error": str(e)})
            except Exception:
                pass

    Thread(target=_run, daemon=True).start()

    # ── respond immediately ────────────────────────────────────────────
    sc = f" ({scenario_hint})" if scenario_hint else ""
    return jsonify({
        "text": (
            f"🔄 *Pipeline queued — {ticket_id}*\n"
            f"*Scenario:* {scenario_hint or 'auto-detect'}{sc}\n"
            f"*Prompt:* {prompt[:100]}\n"
            f"I'll notify <@{user_id}> when complete."
        )
    }), 200


# ---------------------------------------------------------------------------
# Slack interactive message handler (approval buttons)
# ---------------------------------------------------------------------------

# In-memory approval events — keyed by (ticket_id, stage).
# The circuit breaker polls these when waiting for human approval.
_approval_events: dict[str, "threading.Event"] = {}
_approval_results: dict[str, dict] = {}

import threading


def _approval_key(ticket_id: str, stage: str) -> str:
    return f"{ticket_id}|{stage}"


def register_approval_event(ticket_id: str, stage: str) -> threading.Event:
    """Create an Event that the circuit breaker can wait on.
    Returns the Event; set when Slack interactive handler receives the response.
    """
    key = _approval_key(ticket_id, stage)
    evt = threading.Event()
    _approval_events[key] = evt
    return evt


def get_approval_result(ticket_id: str, stage: str) -> dict | None:
    """Return the approval result dict, or None if not yet received."""
    key = _approval_key(ticket_id, stage)
    return _approval_results.get(key)


@app.route("/slack/interactive", methods=["POST"])
def slack_interactive():
    """Receive interactive message payloads from Slack (button clicks).

    Slack sends the payload as a form-encoded ``payload`` parameter containing
    a JSON string with ``type``, ``actions``, ``user``, etc.
    """
    payload_str = request.form.get("payload", "{}")
    try:
        payload = json.loads(payload_str)
    except json.JSONDecodeError:
        return jsonify({"text": "Invalid payload"}), 400

    # Only handle block_actions (button clicks) for now
    if payload.get("type") != "block_actions":
        return "", 200

    actions = payload.get("actions", [])
    user = payload.get("user", {}).get("name", payload.get("user", {}).get("id", "unknown"))

    for action in actions:
        block_id = action.get("block_id", "")
        value = action.get("value", "")

        # Parse block_id: "approval_{ticket_id}_{stage}"
        parts = block_id.split("_", 2)
        if len(parts) >= 3 and parts[0] == "approval":
            ticket_id = parts[1]
            stage = parts[2]

            result = {
                "ticket_id": ticket_id,
                "stage": stage,
                "status": value,  # "approved", "changes_requested", "rejected"
                "approved_by": user,
                "at": datetime.now(timezone.utc).isoformat(),
            }

            # Store result
            key = _approval_key(ticket_id, stage)
            _approval_results[key] = result

            # Unblock any waiting circuit breaker
            evt = _approval_events.get(key)
            if evt:
                evt.set()

            # Acknowledge in Slack
            emoji = {"approved": "✅", "changes_requested": "🔄", "rejected": "❌"}.get(value, "❓")
            slack.status(
                ticket_id,
                f"{emoji} *{stage}* — {value.upper()} by <@{user}>"
            )

    return "", 200


if __name__ == "__main__":
    port = int(get_optional("SLACK_COMMANDS_PORT", "8080"))
    print(f"\n📡 Slack command server → http://localhost:{port}/slack/commands")
    print(f"📊 Viz dashboard       → http://localhost:{port}/viz\n")
    app.run(host="0.0.0.0", port=port, debug=False)  # nosemgrep — required for ngrok tunnel
