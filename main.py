"""
Entry point — run the agentic pipeline with a human prompt.

Usage:
    python main.py "Build a payment confirmation screen POC"
    python main.py --ticket HT-001 "Build a login flow prototype"
"""

import sys
import uuid
import argparse
from datetime import datetime, timezone

from core.state.pipeline_state import new_state
from core.graph.workflow import build


def main():
    parser = argparse.ArgumentParser(description="Hypertech Agentic Pipeline")
    parser.add_argument("prompt", help="The human prompt to process")
    parser.add_argument("--ticket", default=None, help="Ticket ID (auto-generated if omitted)")
    args = parser.parse_args()

    ticket_id = args.ticket or f"HT-{uuid.uuid4().hex[:6].upper()}"
    print(f"\n🚀 Starting pipeline | Ticket: {ticket_id}")
    print(f"   Prompt: {args.prompt}\n")

    state = new_state(ticket_id=ticket_id, prompt=args.prompt)
    graph = build()
    result = graph.invoke(state)

    # Record final output on the pipeline trace and flush all pending spans
    from core.tracing.langfuse import update_pipeline_output, flush
    update_pipeline_output(ticket_id, {
        "status": result["status"],
        "scenario": result["scenario"],
        "branch_url": result.get("coder_output", {}).get("branch_url"),
        "deploy_url": result.get("deploy_url"),
    })
    flush()

    print(f"\n✅ Pipeline complete | Status: {result['status']}")
    if result.get("deploy_url"):
        print(f"   🌐 Live at: {result['deploy_url']}")
    if result.get("coder_output"):
        print(f"   📦 Branch: {result['coder_output'].get('branch_url')}")
    print()


if __name__ == "__main__":
    main()
