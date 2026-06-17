"""
Entry point — run the agentic pipeline with a human prompt.

Usage:
    python main.py "Build a payment confirmation screen POC"
    python main.py --ticket HT-001 "Build a login flow prototype"

Also importable — ``run_pipeline()`` is used by slack_commands for /new.
"""

import sys
import uuid
import argparse
import logging
from datetime import datetime, timezone
from typing import Optional

# Quiet the OTEL background exporter — transient Langfuse export failures are
# retried silently; we don't want them filling stdout during pipeline runs.
logging.getLogger("opentelemetry.sdk.trace.export").setLevel(logging.CRITICAL)

from core.state.pipeline_state import new_state
from core.graph.workflow import build


def run_pipeline(ticket_id: str, prompt: str) -> dict:
    """
    Run the full LangGraph pipeline for a ticket.

    Args:
        ticket_id: The ticket ID (e.g. "HT-AB12CD").
        prompt: The natural-language prompt to process.

    Returns:
        The final PipelineState dict after all agents have run.

    This is the importable entry point — used by both the CLI (main) and
    Slack commands (/new).  It seeds Langfuse models, builds the graph,
    runs it inside a root trace span, and records pipeline output.
    """
    from core.ai.seed_models import seed as seed_models
    seed_models()

    state = new_state(ticket_id=ticket_id, prompt=prompt)
    graph = build()

    from core.tracing.langfuse import pipeline_trace, record_pipeline_output, flush
    with pipeline_trace(ticket_id, prompt):
        result = graph.invoke(state)

        coder_out = result.get("coder_output") or {}
        record_pipeline_output({
            "status": result["status"],
            "scenario": result["scenario"],
            "branch_url": coder_out.get("branch_url"),
            "deploy_url": result.get("deploy_url"),
        })
    flush()

    return result


def main():
    parser = argparse.ArgumentParser(description="Hypertech Agentic Pipeline")
    parser.add_argument("prompt", help="The human prompt to process")
    parser.add_argument("--ticket", default=None, help="Ticket ID (auto-generated if omitted)")
    args = parser.parse_args()

    ticket_id = args.ticket or f"HT-{uuid.uuid4().hex[:6].upper()}"
    print(f"\n🚀 Starting pipeline | Ticket: {ticket_id}")
    print(f"   Prompt: {args.prompt}\n")

    result = run_pipeline(ticket_id, args.prompt)

    coder_out = result.get("coder_output") or {}

    print(f"\n✅ Pipeline complete | Status: {result['status']} | Scenario: {result['scenario']}")
    if coder_out.get("branch_url"):
        print(f"   📦 Branch: {coder_out['branch_url']}")
    if result.get("compliance_report"):
        report = result["compliance_report"]
        print(f"   📋 Compliance: {report.get('overall_status', 'unknown').upper()}")
    if result.get("design_brief"):
        screens = result["design_brief"].get("screens", [])
        print(f"   🎨 Design brief: {len(screens)} screen(s)")

    deploy_url = result.get("deploy_url")
    if deploy_url:
        print(f"   🌐 Live at: {deploy_url}")
        print(f"\n   Server is running — press Ctrl+C to stop.\n")
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n   Server stopped.")
    print()


if __name__ == "__main__":
    main()
