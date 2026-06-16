"""
Slack slash command handlers — responds to /status, /reload-kb, /deploy.

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
import time
from pathlib import Path
from threading import Thread

from flask import Flask, request, jsonify, abort

from core.secrets.loader import get, get_optional
from core.notifications import slack
from ingestion.google_drive import ingest as drive_ingest

app = Flask(__name__)

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
    if request.path == "/slack/commands":
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


if __name__ == "__main__":
    port = int(get_optional("SLACK_COMMANDS_PORT", "8080"))
    print(f"Slack command server running on :{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
