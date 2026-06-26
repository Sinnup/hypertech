"""
Slack slash command handlers — responds to /new, /resume, /status, /checkpoints,
/reload-kb, /deploy, /switch-provider.

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
from core.checkpoint.manager import list_checkpoints, exists as checkpoint_exists

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
    if command == "/answer":
        return _handle_answer(text, user_id)
    if command == "/resume":
        return _handle_resume(text, user_id)
    if command == "/checkpoints":
        return _handle_checkpoints()

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

    # Checkpoint info (if exists)
    if checkpoint_exists(ticket_id):
        from core.checkpoint.manager import load as load_checkpoint
        cp_state = load_checkpoint(ticket_id)
        if cp_state:
            cp_agents = list(cp_state.get("agent_outputs", {}).keys())
            cp_index = cp_state.get("agent_plan_index", "?")
            lines.append(f"*Checkpoint:* index {cp_index} — completed: {' → '.join(cp_agents) or 'none'}")
            lines.append(f"_Resume with:_ `/resume {ticket_id}`")

    return jsonify({"text": "\n".join(lines)}), 200


def _handle_reload_kb(user_id: str):
    def _run():
        try:
            slack.alert(f"🔄 *KB reload started* by <@{user_id}> — fetching from Google Drive…")
            result = drive_ingest.run(
                progress=lambda m: slack.alert(f"🔄 *KB reload*: {m}")
            )
            ingested = result.get("ingested", 0)
            skipped = result.get("skipped", 0)
            errors = result.get("errors", [])
            if ingested == 0:
                detail = errors[0].get("error", "no documents ingested") if errors else "no documents ingested"
                slack.alert(f"⚠️ *KB reload finished with no data* — {detail}")
            else:
                extra = f", {len(errors)} chunk error(s)" if errors else ""
                slack.alert(f"✅ *KB reload complete* — {ingested} chunks ingested ({skipped} skipped{extra}).")
        except Exception as e:  # noqa: BLE001 — surface to Slack, never crash the thread
            slack.alert(f"❌ *KB reload failed*: {e}")

    Thread(target=_run, daemon=True).start()
    return jsonify({"text": "🔄 KB reload started — progress will post to #pipeline-alerts."}), 200


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
            result = run_pipeline(ticket_id, prompt, scenario=scenario_hint)

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
# /resume — resume a pipeline from a saved checkpoint
# ---------------------------------------------------------------------------

def _handle_resume(text: str, user_id: str):
    """
    Resume a pipeline from a saved checkpoint.

    Usage: /resume [ticket_id]

    If *ticket_id* is omitted, the most recent checkpoint is used.
    """
    parts = text.strip().split(maxsplit=1)
    ticket_id = parts[0] if parts else ""
    prompt_override = parts[1] if len(parts) > 1 else ""

    # ── Resolve ticket ────────────────────────────────────────────────────
    if ticket_id:
        if not checkpoint_exists(ticket_id):
            return jsonify({
                "text": f"❌ No checkpoint found for *{ticket_id}*.\n"
                        f"Use `/checkpoints` to see available checkpoints, "
                        f"or `/new <prompt>` to start fresh.",
            }), 200
    else:
        checkpoints = list_checkpoints()
        if not checkpoints:
            return jsonify({
                "text": "❌ No checkpoints available.\n"
                        "Use `/new <prompt>` to start a new pipeline.",
            }), 200
        ticket_id = checkpoints[0]["ticket_id"]

    # ── Launch (async — same pattern as /new) ─────────────────────────────
    def _run():
        try:
            from main import run_pipeline
            result = run_pipeline(ticket_id, prompt_override, resume=True)

            status = result.get("status", "unknown")
            scenario = result.get("scenario", "?")
            deploy_url = result.get("deploy_url")
            coder_out = result.get("coder_output") or {}
            branch_url = coder_out.get("branch_url")
            error = result.get("error")

            if error:
                slack.alert(
                    f"❌ *Resumed pipeline {ticket_id} failed*\n"
                    f"*Scenario:* {scenario}\n"
                    f"*Error:* {error}\n"
                    f"*Triggered by:* <@{user_id}>",
                    channel="#pipeline-alerts",
                )
                return

            lines = [
                f"✅ *Resumed pipeline {ticket_id} complete*",
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
                f"❌ *Resumed pipeline {ticket_id} crashed*\n"
                f"*Error:* {type(e).__name__}: {e}\n"
                f"*Triggered by:* <@{user_id}>",
                channel="#pipeline-alerts",
            )
            try:
                from core.registry import update_ticket
                update_ticket(ticket_id, {"status": "failed", "error": str(e)})
            except Exception:
                pass

    Thread(target=_run, daemon=True).start()

    # ── Respond immediately ───────────────────────────────────────────────
    return jsonify({
        "text": (
            f"♻️ *Pipeline resumed — {ticket_id}*\n"
            f"Continuing from the last checkpoint. "
            f"I'll notify <@{user_id}> when complete."
        )
    }), 200


# ---------------------------------------------------------------------------
# /checkpoints — list all saved checkpoints
# ---------------------------------------------------------------------------

def _handle_checkpoints():
    """
    List all available checkpoints, newest first.

    Usage: /checkpoints
    """
    checkpoints = list_checkpoints()
    if not checkpoints:
        return jsonify({
            "text": "📭 No checkpoints found.\n"
                    "Checkpoints are created automatically as pipelines run — "
                    "use `/new <prompt>` to start one.",
        }), 200

    lines = ["📋 *Saved checkpoints:*"]
    for i, cp in enumerate(checkpoints[:10], 1):  # max 10 to avoid overflow
        agents = " → ".join(cp.get("completed_agents", [])) or "none"
        lines.append(
            f"{i}. *{cp['ticket_id']}* — index {cp.get('agent_plan_index', '?')} "
            f"({agents})"
        )
        if cp.get("saved_at"):
            lines.append(f"   _Saved: {cp['saved_at'][:19]}_")

    if len(checkpoints) > 10:
        lines.append(f"   ... and {len(checkpoints) - 10} more")

    lines.append(f"\nResume with: `/resume <ticket_id>`")
    return jsonify({"text": "\n".join(lines)}), 200


# ---------------------------------------------------------------------------
# /answer — respond to an agent's question
# ---------------------------------------------------------------------------

def _handle_answer(text: str, user_id: str):
    """
    Answer a question from an agent and optionally resume the pipeline.

    Usage: /answer HT-XXXXXX <your response text>

    The answer is stored and can be read by the agent on the next /resume.
    Include ``--resume`` to auto-resume after answering::

        /answer HT-XXXXXX --resume Use Material Design 3 with dark theme
    """
    parts = text.strip().split(maxsplit=1)
    if not parts or not parts[0].startswith("HT-"):
        return jsonify({
            "text": (
                "Usage: `/answer HT-XXXXXX <your response>`\n"
                "Add `--resume` to continue the pipeline after answering:\n"
                "`/answer HT-XXXXXX --resume Use Material Design 3`"
            )
        }), 200

    ticket_id = parts[0]
    response_text = parts[1] if len(parts) > 1 else ""

    # Check for --resume flag
    do_resume = response_text.startswith("--resume ")
    if do_resume:
        response_text = response_text[9:]  # strip "--resume "

    if not response_text:
        return jsonify({
            "text": "Please include your response after the ticket ID.\n"
                    "Usage: `/answer HT-XXXXXX <your response>`"
        }), 200

    # Store the answer in checkpoint state
    answer = {
        "from_user": user_id,
        "text": response_text,
        "at": datetime.now(timezone.utc).isoformat(),
    }

    # Store in approval results so validation_gate can check it
    key = f"{ticket_id}|agent_question"
    _approval_results[key] = {
        "ticket_id": ticket_id,
        "stage": "agent_question",
        "status": "approved",  # answering = approving the question
        "approved_by": user_id,
        "comment": response_text,
        "at": answer["at"],
    }

    # Signal any waiting circuit breaker
    evt = _approval_events.get(key)
    if evt:
        evt.set()

    # Also store in the checkpoint for persistence
    try:
        from core.checkpoint.manager import load as cp_load, save as cp_save
        cp = cp_load(ticket_id)
        if cp:
            answers = cp.get("pending_answers", [])
            answers.append(answer)
            cp["pending_answers"] = answers
            cp_save(ticket_id, cp)
    except Exception:
        pass  # best-effort — answer is also in _approval_results

    if do_resume:
        # Kick off resume in background
        def _run():
            try:
                from main import run_pipeline
                result = run_pipeline(ticket_id, "", resume=True)
                status = result.get("status", "?")
                slack.alert(
                    f"✅ *Pipeline {ticket_id} resumed with answer*\n"
                    f"*Response:* {response_text[:100]}\n"
                    f"*Status:* {status}",
                    channel="#pipeline-alerts",
                )
            except Exception as e:
                slack.alert(
                    f"❌ *Resume failed for {ticket_id}*: {e}",
                    channel="#pipeline-alerts",
                )

        Thread(target=_run, daemon=True).start()

        return jsonify({
            "text": (
                f"💬 *Answer recorded for {ticket_id}*\n"
                f"*Your response:* {response_text[:150]}\n"
                f"♻️ Pipeline automatically resumed."
            )
        }), 200

    return jsonify({
        "text": (
            f"💬 *Answer recorded for {ticket_id}*\n"
            f"*Your response:* {response_text[:150]}\n"
            f"Type `/resume {ticket_id}` to continue the pipeline with your answer."
        )
    }), 200


# ---------------------------------------------------------------------------
# Slack interactive message handler (approval buttons)
# ---------------------------------------------------------------------------

import threading

# Approval state lives in a dedicated module so it is a single shared instance
# even though this file runs as __main__ (python -m core.notifications.slack_commands).
# See core/notifications/approval_store.py for why this matters.
from core.notifications.approval_store import (  # noqa: E402
    _approval_events,
    _approval_results,
    _approval_key,
    register_approval_event,
    get_approval_result,
    store_approval,
)


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

            # Auto-resume: an approval or change-request should continue the
            # pipeline immediately.  Without this the run sits escalated and the
            # Slack channel goes silent until a manual /resume.  A rejection is
            # terminal, so we leave it for the gate to fail cleanly.
            if value in ("approved", "changes_requested"):
                _resume_pipeline_async(ticket_id, reason=f"{stage} {value}")

    return "", 200


def _resume_pipeline_async(ticket_id: str, reason: str = "") -> None:
    """Resume a checkpointed pipeline in a background thread (best-effort).

    Only resumes when a checkpoint actually exists — an escalation checkpoint is
    written by ``stream_pipeline`` precisely so this can pick the run back up.
    """
    def _run():
        try:
            from core.checkpoint.manager import exists as checkpoint_exists
            if not checkpoint_exists(ticket_id):
                slack.status(
                    ticket_id,
                    "⚠️ Approval recorded but no checkpoint found to resume "
                    "— run may have completed or not been checkpointed.",
                )
                return
            slack.status(ticket_id, f"♻️ Resuming pipeline ({reason})…")
            from main import run_pipeline
            result = run_pipeline(ticket_id, "", resume=True)
            slack.status(
                ticket_id,
                f"✅ Pipeline resumed — status: {result.get('status', '?')}",
            )
        except Exception as exc:  # noqa: BLE001 — surface to Slack, never crash server
            slack.alert(
                f"❌ *Auto-resume failed for {ticket_id}*: {exc}",
                channel="#pipeline-alerts",
            )

    Thread(target=_run, daemon=True).start()


if __name__ == "__main__":
    port = int(get_optional("SLACK_COMMANDS_PORT", "8080"))
    print(f"\n📡 Slack command server → http://localhost:{port}/slack/commands")
    print(f"📊 Viz dashboard       → http://localhost:{port}/viz\n")
    app.run(host="0.0.0.0", port=port, debug=False)  # nosemgrep — required for ngrok tunnel
