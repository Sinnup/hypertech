"""
Infra agent — serves the generated POC HTML locally, optionally exposes via ngrok,
posts the live URL to Slack #deployments.

POC path: Python HTTP server on a random port; ngrok tunnel if NGROK_AUTH_TOKEN is set.
Set ``NGROK_DOMAIN`` to use a static ngrok domain (free-tier: one per account).
"""

import json
import os
import threading
import socket
import http.server
import functools
from pathlib import Path
from datetime import datetime, timezone

from langfuse import observe

from core.ai import ModelTier
from core.state.pipeline_state import PipelineState, agent_message
from core.agent_registry import register
from core.notifications import slack
from core.secrets.loader import get_optional
from core.tracing.langfuse import get_client

_REGISTRY_PATH = Path(get_optional("FEATURE_REGISTRY_PATH", "features/feature-registry.json"))


def _update_registry(ticket_id: str, updates: dict):
    registry = json.loads(_REGISTRY_PATH.read_text()) if _REGISTRY_PATH.exists() else {}
    if ticket_id in registry:
        registry[ticket_id].update(updates)
        registry[ticket_id]["last_updated"] = datetime.now(timezone.utc).isoformat()
        _REGISTRY_PATH.write_text(json.dumps(registry, indent=2))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _serve(directory: str, port: int):
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler,
        directory=directory,
    )
    server = http.server.HTTPServer(("0.0.0.0", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _ngrok_tunnel(port: int) -> str | None:
    """Start an ngrok tunnel and return the public URL, or None if not configured.

    Uses ``NGROK_DOMAIN`` (static domain) when set, falling back to a random
    ngrok URL.  Requires ``NGROK_AUTH_TOKEN``.
    """
    token = get_optional("NGROK_AUTH_TOKEN")
    if not token:
        return None

    domain = get_optional("NGROK_DOMAIN")  # e.g. "unplug-active-observing.ngrok-free.dev"

    try:
        import ngrok  # pip install ngrok
        kwargs = {"port": port, "authtoken": token}
        if domain:
            kwargs["domain"] = domain
        listener = ngrok.forward(**kwargs)
        return listener.url()
    except ImportError:
        # Fall back to pyngrok if ngrok SDK not installed
        try:
            from pyngrok import ngrok as pyngrok, conf
            conf.get_default().auth_token = token
            kwargs = {"port": port}
            if domain:
                kwargs["hostname"] = domain
            tunnel = pyngrok.connect(**kwargs)
            return tunnel.public_url
        except ImportError:
            return None


@register("infra", description="Serves POC locally via HTTP server + ngrok tunnel; handles mobile project artifacts",
          tier=ModelTier.FAST, tags=["deployment", "infrastructure"])
@observe(name="infra-agent")
def run(state: PipelineState) -> PipelineState:
    ticket_id = state["ticket_id"]
    coder_output = state.get("coder_output") or {}

    slack.status(ticket_id, "🏗️ Infra agent started — preparing local deployment...")

    project_type = coder_output.get("project_type", "web")

    if project_type == "android":
        return _handle_android(state, ticket_id, coder_output)

    if project_type == "backend":
        return _handle_backend(state, ticket_id, coder_output)

    return _handle_web(state, ticket_id, coder_output)


def _handle_android(state: PipelineState, ticket_id: str, coder_output: dict) -> PipelineState:
    """Post build instructions for Android project — no HTTP server needed."""
    project_dir = coder_output.get("project_dir", "")
    build_cmd = coder_output.get("build_cmd", "")
    apk_path = coder_output.get("apk_path", "")
    files = coder_output.get("files", [])

    slack.status(ticket_id, f"📱 Android project at: `{project_dir}`")
    slack.status(
        ticket_id,
        f"🔨 *Build APK:*\n```\n{build_cmd}\n```\n"
        f"📲 *Install:*\n```\nadb install {apk_path}\n```\n"
        f"📁 *Files generated:* {len(files)}"
    )

    _update_registry(ticket_id, {
        "status": "deployed",
        "project_dir": project_dir,
    })

    state["current_agent"] = "infra"
    state["next_agent"] = None
    state["status"] = "deployed"
    state["deploy_url"] = f"file://{project_dir}"
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    state["agent_messages"].append(
        agent_message("infra", "human", "deployed", ticket_id, {
            "project_type": "android",
            "project_dir": project_dir,
            "build_cmd": build_cmd,
        })
    )

    return state


def _handle_backend(state: PipelineState, ticket_id: str, coder_output: dict) -> PipelineState:
    """Post run instructions for FastAPI backend."""
    project_dir = coder_output.get("project_dir", "")
    run_cmd = coder_output.get("run_cmd", "")

    slack.status(
        ticket_id,
        f"⚙️ *Backend project at:* `{project_dir}`\n"
        f"*Run:*\n```\npip install -r {project_dir}/requirements.txt\n{run_cmd}\n```\n"
        f"*Docs:* http://localhost:8000/docs"
    )

    _update_registry(ticket_id, {"status": "deployed", "project_dir": project_dir})

    state["current_agent"] = "infra"
    state["next_agent"] = None
    state["status"] = "deployed"
    state["deploy_url"] = "http://localhost:8000"
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    state["agent_messages"].append(
        agent_message("infra", "human", "deployed", ticket_id, {
            "project_type": "backend",
            "project_dir": project_dir,
            "run_cmd": run_cmd,
        })
    )

    return state


def _handle_web(state: PipelineState, ticket_id: str, coder_output: dict) -> PipelineState:
    """Serve HTML prototype via HTTP + ngrok."""
    local_path = coder_output.get("local_path")
    if not local_path or not Path(local_path).exists():
        fallback = Path(f"poc/{state['ticket_id']}/index.html")
        if fallback.exists():
            local_path = str(fallback.resolve())
        else:
            slack.status(state["ticket_id"], "⚠️ Infra agent: no HTML file found — skipping deployment.")
            state["current_agent"] = "infra"
            state["next_agent"] = None
            state["status"] = "deploy_skipped"
            state["last_updated"] = datetime.now(timezone.utc).isoformat()
            return state

    serve_dir = str(Path(local_path).parent)
    port = _free_port()
    _serve(serve_dir, port)

    local_url = f"http://localhost:{port}/index.html"
    public_url = _ngrok_tunnel(port) or local_url
    deploy_url = f"{public_url}/index.html" if public_url != local_url else local_url

    client = get_client()
    if client:
        client.update_current_span(output={"deploy_url": deploy_url, "port": port})

    slack.deployment(ticket_id, deploy_url)
    slack.status(ticket_id, f"✅ POC live — {deploy_url}")

    _update_registry(ticket_id, {
        "status": "deployed",
        "agents_involved": list(state.get("agent_outputs", {}).keys()) + ["infra"],
        "deploy_url": deploy_url,
    })

    state["current_agent"] = "infra"
    state["next_agent"] = None
    state["status"] = "deployed"
    state["deploy_url"] = deploy_url
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    state["agent_messages"].append(
        agent_message("infra", "human", "deployed", ticket_id, {"deploy_url": deploy_url})
    )

    return state
