"""Slack notifications — status broadcasting and HitL approval messages."""

import requests
from core.secrets.loader import get, get_optional


def _webhook() -> str | None:
    url = get_optional("SLACK_WEBHOOK_URL", "")
    return url.strip() if url else None


def post(channel: str, text: str, blocks: list = None) -> bool:
    url = _webhook()
    if not url:
        return False  # Slack not configured — silently skip
    payload = {"channel": f"#{channel}", "text": text}
    if blocks:
        payload["blocks"] = blocks
    try:
        r = requests.post(url, json=payload, timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def alert(text: str, channel: str = "pipeline-alerts") -> bool:
    """Post a high-priority alert. No ticket ID prefix — freeform message."""
    return post(channel, text)


def status(ticket_id: str, message: str) -> bool:
    channel = get_optional("SLACK_CHANNEL_STATUS", "agent-status")
    return post(channel, f"[{ticket_id}] {message}")


def approval_request(ticket_id: str, stage: str, summary: str) -> bool:
    channel = get_optional("SLACK_CHANNEL_APPROVALS", "human-approvals")
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*[{ticket_id}] Approval needed — {stage}*\n{summary}"}},
        {
            "type": "actions",
            "block_id": f"approval_{ticket_id}_{stage}",
            "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "✅ Approve"}, "style": "primary", "value": "approved"},
                {"type": "button", "text": {"type": "plain_text", "text": "🔄 Request Changes"}, "value": "changes_requested"},
                {"type": "button", "text": {"type": "plain_text", "text": "❌ Reject"}, "style": "danger", "value": "rejected"},
            ],
        },
    ]
    return post(channel, f"[{ticket_id}] Approval needed: {stage}", blocks)


def deployment(ticket_id: str, url: str) -> bool:
    channel = get_optional("SLACK_CHANNEL_DEPLOYMENTS", "deployments")
    return post(channel, f"[{ticket_id}] 🚀 Live at: {url}")
