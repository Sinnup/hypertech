# QA Agent

Role: Testing strategy, verification, code review, and quality gates for the Hypertech SDLC pipeline.

## Quality Gates

The pipeline has built-in quality stages:

```
Production path:
  orchestrator → ba_compliance → ux_ui → architect → pr_review → security → END
                                                    ↑            ↑
                                              HitL approval   2 review gates
```

### Gate 1: Architect (HitL — Human in the Loop)

- Generates HLD JSON from requirements + design brief
- Posts to Slack for human approval (`#architect-reviews`)
- Blocks pipeline until approval (approved/changes_requested/rejected)
- If `changes_requested` → pipeline waits for updated requirements
- Configurable via `core/notifications/slack_commands.py`

### Gate 2: PR Review Agent

- Reviews code style + architecture after coder generates code
- Checks: naming conventions, structure, DRY violations, security patterns
- Out of scope: functional correctness (that's for tests), deep security (goes to security agent)
- Can block security step if review status is "blocked"

### Gate 3: Security Agent

- Runs Semgrep SAST on generated code
- LLM review for business logic vulnerabilities
- Posts findings to Slack `#security-alerts`
- Severity: "low" / "medium" / "high" / "critical"

## Testing Strategy

### Manual Verification

```bash
# Quick smoke test — POC path
python main.py "POC: payment confirmation screen"

# Production path test
python main.py --ticket HT-SMOKE-01 "Build KYC compliance for production fintech"

# Verify Langfuse shows: pipeline-run trace with nested agent spans
# Verify feature-registry.json updated for each agent
# Verify Slack notifications (if configured)
```

### What to Verify After Changes

1. **Agent changes**: Run both POC and Production paths — verify state transitions
2. **AI provider changes**: Run same prompt with both `AI_PROVIDER=claude` and `AI_PROVIDER=deepseek`
3. **Tracing changes**: Check Langfuse UI for complete trace with token counts
4. **Workflow changes**: Verify routing for all 3 scenarios (poc/internal/production)
5. **State changes**: Verify `PipelineState` keys are set/read correctly across agents

### Common Failure Modes

| Symptom | Likely Cause |
|---------|-------------|
| `ModuleNotFoundError: langfuse.decorators` | v2 API in code — use v4 APIs only (see [[langfuse-v4-tracing]]) |
| Langfuse shows no agent spans | Credentials invalid or server not running |
| Agent doesn't execute | Workflow routing error — check `next_agent` value |
| LLM call hangs | API key missing or rate limited |
| JSON parse error | LLM output not valid JSON — fix prompt or use `strip_fences()` |

### PR Review Checklist

When reviewing a PR in this repo:
- [ ] All new agents follow the pattern: `@observe(name="...") def run(state):`
- [ ] No hardcoded model IDs — use `get_llm(tier)` or `get_model_id(tier)`
- [ ] Langfuse calls use v4 API (`update_current_generation`, not `langfuse_context`)
- [ ] State keys are documented and backward-compatible
- [ ] New agents update feature registry
- [ ] Agent docstring explains role and tier
- [ ] `.env.example` updated for new env vars
- [ ] `CLAUDE.md` updated if architecture changes

## Related Files

- `agents/pr_review/agent.py` — Automated PR review
- `agents/security/agent.py` — Security review + Semgrep
- `agents/qa/agent.py` — QA agent
- `.github/workflows/ci.yml` — CI pipeline
