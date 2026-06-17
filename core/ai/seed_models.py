"""
Seed DeepSeek model definitions in Langfuse so cost tracking works.

Langfuse only auto-prices OpenAI, Anthropic, and Google models.  DeepSeek
models must be registered manually via the API.  This script is idempotent —
it checks for existing models before creating.

Run once (or on startup):
    python -m core.ai.seed_models

Pricing source: https://api-docs.deepseek.com/quick_start/pricing
"""

import os, json, urllib.request, urllib.error

from core.secrets.loader import get_optional


LANGFUSE_HOST = get_optional("LANGFUSE_HOST", "http://localhost:3000")
PUBLIC_KEY = get_optional("LANGFUSE_PUBLIC_KEY")
SECRET_KEY = get_optional("LANGFUSE_SECRET_KEY")

# DeepSeek model pricing — per-token USD
# deepseek-chat: $0.14/M input, $0.28/M output
# deepseek-reasoner: $0.55/M input, $2.19/M output
MODELS = [
    {
        "modelName": "deepseek-chat",
        "matchPattern": "(?i)^(deepseek-chat)$",
        "unit": "TOKENS",
        "inputPrice": 0.14 / 1_000_000,
        "outputPrice": 0.28 / 1_000_000,
        "tokenizerId": "openai",  # DeepSeek uses OpenAI-compatible tokenizer
    },
    {
        "modelName": "deepseek-reasoner",
        "matchPattern": "(?i)^(deepseek-reasoner)$",
        "unit": "TOKENS",
        "inputPrice": 0.55 / 1_000_000,
        "outputPrice": 2.19 / 1_000_000,
        "tokenizerId": "openai",
    },
]


def _auth_header() -> str:
    import base64
    credentials = f"{PUBLIC_KEY}:{SECRET_KEY}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return f"Basic {encoded}"


def _existing_models() -> set[str]:
    """Return the set of model names already registered in Langfuse."""
    url = f"{LANGFUSE_HOST}/api/public/models"
    req = urllib.request.Request(url, headers={"Authorization": _auth_header()})
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
        return {m["modelName"] for m in data.get("data", [])}
    except urllib.error.HTTPError as e:
        print(f"  ⚠️  Could not fetch existing models: {e.code} {e.reason}")
        return set()


def seed():
    """Register any missing DeepSeek models.  Idempotent."""
    if not PUBLIC_KEY or not SECRET_KEY:
        print("  ⚠️  Langfuse keys not configured — skipping model seeding.")
        return

    existing = _existing_models()
    created = 0

    for model in MODELS:
        name = model["modelName"]
        if name in existing:
            print(f"  ✅ {name} already registered")
            continue

        url = f"{LANGFUSE_HOST}/api/public/models"
        payload = json.dumps(model).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": _auth_header(),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as resp:
                if resp.status == 201 or resp.status == 200:
                    print(
                        f"  ✅ {name} registered — "
                        f"in: ${model['inputPrice']:.8f}/tok, "
                        f"out: ${model['outputPrice']:.8f}/tok"
                    )
                    created += 1
                else:
                    print(f"  ❌ {name}: HTTP {resp.status}")
        except urllib.error.HTTPError as e:
            body = e.read().decode() if e.fp else ""
            print(f"  ❌ {name}: HTTP {e.code} — {body[:200]}")

    if created == 0:
        print("  All DeepSeek models already registered.")
    else:
        print(f"  Registered {created} new model(s).")


if __name__ == "__main__":
    print("Seeding Langfuse model definitions...")
    seed()
