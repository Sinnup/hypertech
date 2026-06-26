"""
Entry point — run the agentic pipeline with a human prompt.

Usage:
    python main.py "Build a payment confirmation screen POC"
    python main.py --ticket HT-001 "Build a login flow prototype"
    python main.py --resume                    # resume most recent checkpoint
    python main.py --resume --ticket HT-001    # resume specific ticket

Also importable — ``run_pipeline()`` is used by slack_commands for /new.
"""

import os
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


def run_pipeline(
    ticket_id: str,
    prompt: str,
    resume: bool = False,
    scenario: Optional[str] = None,
) -> dict:
    """
    Run the full LangGraph pipeline for a ticket.

    Args:
        ticket_id: The ticket ID (e.g. "HT-AB12CD").
        prompt: The natural-language prompt to process (ignored on resume
                unless the checkpoint is missing — the saved prompt is used).
        resume: If True, attempt to load a checkpoint for *ticket_id*.

    Returns:
        The final PipelineState dict after all agents have run.

    This is the importable entry point — used by both the CLI (main) and
    Slack commands (/new).  It seeds Langfuse models, builds the graph,
    runs it inside a root trace span, and records pipeline output.
    """
    from core.ai.seed_models import seed as seed_models
    seed_models()

    # Enable LangSmith auto-tracing (env-var-based, no manual callback needed).
    from core.tracing.langsmith import ensure as ensure_langsmith
    ensure_langsmith()

    # Start the viz server (daemon thread — idempotent, no-op if already running).
    from core.server.viz_server import start_viz_server
    start_viz_server()

    # ── Checkpoint detection ──────────────────────────────────────────────
    from core.checkpoint.manager import load as load_checkpoint, exists as checkpoint_exists

    checkpoint = None
    if resume or checkpoint_exists(ticket_id):
        checkpoint = load_checkpoint(ticket_id)

    if checkpoint is not None:
        state = checkpoint
        print(f"   ♻️  Resuming from checkpoint (plan_index={state.get('agent_plan_index')})")
        completed = list(state.get("agent_outputs", {}).keys())
        if completed:
            print(f"   Completed agents: {' → '.join(completed)}")
        # Use CLI prompt if explicitly provided, otherwise keep saved prompt.
        if prompt:
            state["human_prompt"] = prompt
    else:
        if resume and not checkpoint_exists(ticket_id):
            print(f"   ⚠️  No checkpoint found for {ticket_id} — starting fresh")
        state = new_state(ticket_id=ticket_id, prompt=prompt, scenario=scenario)

    graph = build()

    from core.tracing.langfuse import pipeline_trace, record_pipeline_output, flush
    from core.events.graph_events import stream_pipeline, emit_event
    from datetime import datetime, timezone as tz

    # With validation_gate + context_packer, each agent step is 3 graph nodes.
    # A 7-agent production pipeline needs ~21 steps; set limit generously.
    recursion_limit = int(os.getenv("PIPELINE_RECURSION_LIMIT", "35"))

    try:
        with pipeline_trace(ticket_id, prompt or state.get("human_prompt", "")):
            # LangGraph native streaming — emits viz events from each node chunk.
            result = stream_pipeline(graph, state, ticket_id, recursion_limit=recursion_limit)

            coder_out = result.get("coder_output") or {}
            record_pipeline_output({
                "status": result["status"],
                "scenario": result["scenario"],
                "branch_url": coder_out.get("branch_url"),
                "deploy_url": result.get("deploy_url"),
            })
        flush()

        emit_event(ticket_id, "pipeline_complete", {
            "status": result["status"],
            "timestamp": datetime.now(tz.utc).isoformat(),
        })
        return result

    except Exception as exc:
        emit_event(ticket_id, "pipeline_error", {
            "error": str(exc),
            "timestamp": datetime.now(tz.utc).isoformat(),
        })
        raise


def main():
    parser = argparse.ArgumentParser(description="Hypertech Agentic Pipeline")
    parser.add_argument(
        "prompt", nargs="?", default="",
        help="The human prompt to process (optional when --resume)",
    )
    parser.add_argument(
        "--ticket", default=None,
        help="Ticket ID (auto-generated if omitted)",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from a saved checkpoint",
    )
    args = parser.parse_args()

    from core.checkpoint.manager import list_checkpoints, exists as checkpoint_exists

    # ── Resolve ticket_id and prompt ──────────────────────────────────────
    if args.resume and not args.ticket:
        # Find most recent checkpoint
        checkpoints = list_checkpoints()
        if not checkpoints:
            print("❌ No checkpoints found (use --ticket or omit --resume to start fresh)")
            sys.exit(1)
        ticket_id = checkpoints[0]["ticket_id"]
        latest = checkpoints[0]
        prompt = args.prompt  # may be empty — checkpoint has the saved prompt
        print(f"   Resuming most recent checkpoint: {ticket_id}")
        print(f"   Saved at: {latest['saved_at']}")
        if latest.get("completed_agents"):
            print(f"   Completed agents: {' → '.join(latest['completed_agents'])}")
    elif args.ticket:
        ticket_id = args.ticket
        prompt = args.prompt
        # Auto-detect existing checkpoint
        if not args.resume and checkpoint_exists(ticket_id):
            print(f"   ℹ️  Found existing checkpoint for {ticket_id} — will resume")
            print(f"   (Use a different --ticket or delete the checkpoint to start fresh)")
    else:
        ticket_id = f"HT-{uuid.uuid4().hex[:6].upper()}"
        prompt = args.prompt

    if not prompt and not args.resume and not checkpoint_exists(ticket_id if args.ticket else ""):
        print("❌ A prompt is required for a new pipeline.")
        sys.exit(1)

    # ── Start ─────────────────────────────────────────────────────────────
    is_resume = args.resume or (args.ticket and checkpoint_exists(args.ticket or ""))
    prefix = "♻️" if is_resume else "🚀"
    print(f"\n{prefix} Starting pipeline | Ticket: {ticket_id}")
    if prompt:
        print(f"   Prompt: {prompt}\n")

    result = run_pipeline(ticket_id, prompt, resume=args.resume)

    coder_out = result.get("coder_output") or {}

    print(f"\n✅ Pipeline complete | Status: {result['status']} | Scenario: {result['scenario']}")
    if result.get("agent_plan"):
        print(f"   📋 Plan: {' → '.join(result['agent_plan'])}")
    if result.get("confidence_scores"):
        scores = result["confidence_scores"]
        print(f"   📊 Confidence: {', '.join(f'{k}={v:.0%}' for k, v in scores.items())}")
    if coder_out.get("branch_url"):
        print(f"   📦 Branch: {coder_out['branch_url']}")
    if result.get("compliance_report"):
        report = result["compliance_report"]
        print(f"   📋 Compliance: {report.get('overall_status', 'unknown').upper()}")
    if result.get("design_brief"):
        screens = result["design_brief"].get("screens", [])
        print(f"   🎨 Design brief: {len(screens)} screen(s)")
    if result.get("context_summaries"):
        print(f"   📝 Context summaries: {len(result['context_summaries'])} agent(s)")

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
