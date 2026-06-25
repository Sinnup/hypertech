"""
Real end-to-end scenario runner (manual / final-validation use).

Drives the actual LangGraph pipeline with real LLM calls, using HITL_AUTOPILOT
to self-drive human approvals.  NOT collected by pytest (filename isn't
``test_*``) so it never burns tokens in a normal test run.

Usage:
    uv run python tests/e2e/run_scenarios.py mobile --autopilot approve
    uv run python tests/e2e/run_scenarios.py web    --autopilot changes
    uv run python tests/e2e/run_scenarios.py backend --autopilot approve
    uv run python tests/e2e/run_scenarios.py reject  --autopilot reject

Token thrift: forces AI_PROVIDER=deepseek and a tight recursion limit unless
already set in the environment.
"""

import os
import sys
import argparse
import secrets

# Token-thrift + tracing-safe defaults (set before importing the pipeline).
os.environ.setdefault("AI_PROVIDER", "deepseek")
os.environ.setdefault("PIPELINE_RECURSION_LIMIT", "20")

# Short, scenario-steering prompts (keyword routing in the orchestrator).
SCENARIOS = {
    "mobile": (
        "poc",
        "Build a tiny Android TPV demo app in Kotlin/Compose. A single button "
        "press simulates card insertion and shows 'Payment processed'. Deliver an APK.",
    ),
    "web": (
        "poc",
        "Build a tiny single-page web prototype: a button that simulates a "
        "payment and shows a success message. HTML + Tailwind + JS.",
    ),
    "backend": (
        "poc",
        "Build a tiny FastAPI backend with one POST /pay endpoint that returns "
        "{'status': 'approved'}. Include a health endpoint.",
    ),
    "compliance": (
        "internal",
        "Build an internal payment-status web tool. Needs a compliance review.",
    ),
}


def run(scenario: str, autopilot: str) -> dict:
    if scenario not in SCENARIOS:
        raise SystemExit(f"Unknown scenario {scenario!r}; choose from {list(SCENARIOS)}")

    os.environ["HITL_AUTOPILOT"] = autopilot
    _scenario_hint, prompt = SCENARIOS[scenario]

    from core.agent_registry import hitl_autopilot
    hitl_autopilot.reset()

    from main import run_pipeline
    ticket = f"HT-E2E{secrets.token_hex(2).upper()}"
    print(f"\n🧪 e2e | scenario={scenario} | autopilot={autopilot} | ticket={ticket}")
    result = run_pipeline(ticket, prompt)

    print(f"\n— result —")
    print(f"  status:   {result.get('status')}")
    print(f"  scenario: {result.get('scenario')}")
    print(f"  plan:     {' → '.join(result.get('agent_plan', []))}")
    print(f"  deploy:   {result.get('deploy_url')}")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", choices=list(SCENARIOS))
    ap.add_argument("--autopilot", default="approve",
                    choices=["approve", "reject", "changes", "idle"])
    args = ap.parse_args()
    run(args.scenario, args.autopilot)


if __name__ == "__main__":
    main()
