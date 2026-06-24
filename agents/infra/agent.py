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
from langsmith import traceable

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


def _list_poc_files(ticket_id: str) -> str:
    """Return a human-readable summary of files in the poc directory."""
    poc_dir = Path(f"poc/{ticket_id}")
    if not poc_dir.exists():
        return "(no poc directory)"
    files = list(poc_dir.rglob("*"))
    if not files:
        return "(empty)"
    # Show first 5 filenames
    names = [str(f.relative_to(poc_dir)) for f in files[:5] if f.is_file()]
    suffix = f" +{len(files) - 5} more" if len(files) > 5 else ""
    return ", ".join(names) + suffix


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


def _detect_from_filesystem(ticket_id: str, coder_output: dict) -> str:
    """
    Detect project type by inspecting files in ``poc/{ticket_id}/``.

    Returns ``"android"``, ``"backend"``, ``"web"``, or ``""`` (unknown).
    Prefers explicit hints from *coder_output* when available, then falls
    back to filesystem heuristics.
    """
    poc_dir = Path(f"poc/{ticket_id}")

    # 1. Check for explicit file hints from the coder
    if coder_output.get("apk_path"):
        return "android"
    if coder_output.get("run_cmd") and ".py" in str(coder_output.get("run_cmd", "")):
        return "backend"

    # 2. Filesystem heuristics — walk poc dir for known patterns
    if not poc_dir.exists():
        return ""

    all_files = list(poc_dir.rglob("*"))
    all_names = {f.name.lower() for f in all_files}
    all_suffixes = {f.suffix.lower() for f in all_files}

    # Android: build.gradle(.kts), AndroidManifest.xml, .kt/.java/.kts files
    android_indicators = {"build.gradle", "build.gradle.kts", "androidmanifest.xml"}
    android_code = {".kt", ".java", ".kts"}
    if android_indicators & all_names or android_code & all_suffixes:
        return "android"

    # Backend: requirements.txt, pyproject.toml, main.py in root, FastAPI patterns
    backend_files = {"requirements.txt", "pyproject.toml", "main.py", "app.py"}
    if backend_files & all_names:
        return "backend"
    # Check for a Python project directory with multiple .py files
    py_files = [f for f in all_files if f.suffix == ".py"]
    if len(py_files) >= 2:
        return "backend"

    # Web: index.html, .html files, tailwind.config.js
    if "index.html" in all_names or ".html" in all_suffixes:
        return "web"

    # Check subdirectories too (coder_mobile puts files in android/)
    for sub in poc_dir.iterdir():
        if sub.is_dir():
            sub_files = list(sub.rglob("*"))
            sub_names = {f.name.lower() for f in sub_files}
            sub_suffixes = {f.suffix.lower() for f in sub_files}
            if android_indicators & sub_names or android_code & sub_suffixes:
                return "android"
            if {"requirements.txt", "main.py"} & sub_names:
                return "backend"
            if "index.html" in sub_names or ".html" in sub_suffixes:
                return "web"

    return ""


@register("infra", description="Serves POC locally via HTTP server + ngrok tunnel; handles mobile project artifacts",
          tier=ModelTier.FAST, tags=["deployment", "infrastructure"])
@observe(name="infra-agent")
@traceable(name="infra", run_type="chain")
def run(state: PipelineState) -> PipelineState:
    ticket_id = state["ticket_id"]
    coder_output = state.get("coder_output") or {}

    slack.status(ticket_id, "🏗️ Infra agent started — preparing deployment...")

    project_type = coder_output.get("project_type", "")

    # ── Android ───────────────────────────────────────────────────────────
    if project_type == "android":
        return _handle_android(state, ticket_id, coder_output)

    # ── Backend ───────────────────────────────────────────────────────────
    if project_type == "backend":
        return _handle_backend(state, ticket_id, coder_output)

    # ── Web (explicit) ────────────────────────────────────────────────────
    if project_type == "web":
        return _handle_web(state, ticket_id, coder_output)

    # ── Unknown / missing project_type — detect from filesystem ──────────
    detected = _detect_from_filesystem(ticket_id, coder_output)
    if detected == "android":
        return _handle_android(state, ticket_id, coder_output)
    if detected == "backend":
        return _handle_backend(state, ticket_id, coder_output)
    if detected == "web":
        return _handle_web(state, ticket_id, coder_output)

    # ── Nothing found ─────────────────────────────────────────────────────
    slack.status(
        ticket_id,
        f"⚠️ Infra: unknown project type '{project_type or '?'}' "
        f"and no recognizable artifacts found in poc/{ticket_id}/. "
        f"Skipping deployment.",
    )
    state["current_agent"] = "infra"
    state["next_agent"] = None
    state["status"] = "deploy_skipped"
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    return state


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
    """Serve HTML prototype via HTTP + ngrok.

    If the declared HTML file is missing, searches the poc directory for
    any .html file before giving up.
    """
    local_path = coder_output.get("local_path")
    if not local_path or not Path(local_path).exists():
        # Search for any HTML file in the poc directory
        poc_dir = Path(f"poc/{ticket_id}")
        candidates = list(poc_dir.rglob("*.html")) if poc_dir.exists() else []
        if candidates:
            local_path = str(candidates[0].resolve())
        else:
            slack.status(
                state["ticket_id"],
                f"⚠️ Infra: no HTML file found in poc/{ticket_id}/. "
                f"Files present: {_list_poc_files(ticket_id)}",
            )
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
