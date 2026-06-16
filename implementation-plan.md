# Agentic SDLC — Implementation Plan
**Version:** 1.3  
**Date:** 2026-06-13  
**Context:** POC for Hypertech CTO proposal — budget-optimized, Claude-first  
**Sprint:** Monday–Thursday (4 days). Target: working demo by Wednesday, presentation-ready by Thursday EOD.

---

## 1. Guiding Principles

- **Cheapest viable tool** at each layer; upgrade paths are noted but not required for POC.
- **Local-first**: everything runs locally via Docker Compose for dev and POC. AWS is only introduced at cert and prod stages.
- **Claude-first**: Haiku for routing and trivial tasks, Sonnet for standard work, Opus only for architecture and deep compliance analysis.
- **No hardcoded secrets** anywhere, not even in markdown files.
- **Everything observable**: every agent action flows through Langfuse + Slack.
- **Human-in-the-loop is async**: approvals happen via Slack interactive messages, not blocking the pipeline.

---

## 2. Stack Selection (Budget-Optimized)

| Layer | Tool | Cost | Notes |
|---|---|---|---|
| LLM | Claude Haiku / Sonnet / Opus | Pay-per-use | Haiku ~$0.08/MTok input; Sonnet ~$3/MTok |
| Orchestration | LangGraph (OSS) | Free | Stateful, resumable, human-in-the-loop native. Phase 2 evolution → Koog (Kotlin-native agents, see §15) |
| Agent Tracing | Langfuse v3 (local Docker Compose) | Free | Self-hosted locally, same compose stack as ChromaDB. OTEL-native in v4 Python SDK. Model cost catalog includes Claude 4.x. |
| Notifications + HitL | Slack | Free tier | Webhooks + Slack app with interactive buttons |
| Source Control | GitHub | Free | Public or private repo |
| CI/CD | GitHub Actions | Free (2k min/mo private) | Enough for POC |
| Feature Tracking | GitHub Issues + Projects | Free | Kanban board, ticket linking |
| Secrets (local/dev) | `.env` / `.properties` files | Free | Git-ignored, never committed |
| Secrets (cert/prod) | AWS Secrets Manager | ~$0.40/secret/mo | AWS credits; only provisioned when deploying to cert or prod |
| Vector Store | ChromaDB (local Docker Compose) | Free | Same compose stack; persistent volume |
| Regulation Docs Source | Google Drive API | Free | Reads from a designated Drive folder |
| Doc Ingestion Pipeline | LangChain document loaders | Free (OSS) | Parses Drive docs → ChromaDB |
| Deployment (local/dev) | Docker Compose | Free | Full stack runs on local machine |
| Deployment (cert/prod) | AWS Lambda + API Gateway | Credits | Introduced only at cert and prod stages |
| Container Registry (cert/prod) | AWS ECR | Free tier (500 MB) | Only needed for cert/prod builds |
| Infrastructure (cert/prod) | ECS Fargate | AWS credits | Not needed for POC |
| Monitoring (cert/prod) | AWS CloudWatch | AWS credits | Local logs via Docker Compose for dev |

**Environment summary:**

| Environment | Where | When |
|---|---|---|
| Local / dev / POC | Docker Compose on your machine | Now — Monday through demo |
| Cert | AWS (Lambda + ECR + Secrets Manager) | After POC is validated |
| Prod | AWS (ECS Fargate + full infra) | Client-ready releases |

**Total POC cost:** ~$0 infrastructure + LLM token usage only (< $10 at POC call volume).

---

## 3. Repository Structure

```
hypertech-agentic/
├── .github/
│   ├── workflows/          # GitHub Actions CI/CD pipelines
│   └── ISSUE_TEMPLATE/     # Ticket templates per scenario
├── agents/
│   ├── orchestrator/       # Lead agent + intent classifier
│   ├── ba_compliance/      # Business Analyst + Compliance agent
│   ├── architect/          # HLD + tech stack decisions
│   ├── ux_ui/              # Design brief generation (structured JSON, no Figma for POC)
│   ├── coder/              # Code generation agent
│   ├── security/           # SAST scan orchestration
│   ├── qa/                 # Test generation + coverage validation
│   ├── infra/              # Docker/K8s deployment agent
│   ├── support/            # CLI installs, tool setup
│   ├── knowledge_base/     # KB agent + ChromaDB interface
│   └── pr_review/          # Code review agent (pre-security)
├── core/
│   ├── graph/              # LangGraph workflow definitions
│   ├── state/              # Shared pipeline state schema
│   ├── routing/            # Intent classification logic
│   ├── memory/             # ChromaDB client + query helpers
│   ├── notifications/      # Slack integration (webhooks + interactivity)
│   └── secrets/            # Secrets loader (local .env → AWS Secrets Manager)
├── ingestion/
│   └── google_drive/       # Drive → ChromaDB regulation doc pipeline
├── features/
│   └── feature-registry.json  # Structured feature/ticket log
├── changelogs/             # Per-project indexed changelogs (queryable)
├── config/
│   ├── local.properties    # Local non-secret config
│   ├── dev.properties
│   └── prod.properties
├── .env.example            # Template only, no real values
├── docker-compose.yml      # Local stack (ChromaDB, agents)
└── README.md
```

---

## 4. Agent Roster and Responsibilities

### 4.1 Orchestrator (Lead Agent)
- **Model:** Sonnet (routing decisions) / Haiku (state reads)
- **Responsibilities:** Intent classification, pipeline routing, project state reads/writes, feature registry updates, human-in-the-loop escalations.
- **Owns:** `core/routing/`, `features/feature-registry.json`
- **Key rule:** Never executes domain work directly — only delegates.

### 4.2 BA / Compliance Agent
- **Model:** Sonnet (analysis) / Opus (deep compliance)
- **Responsibilities:** Extract requirements from meeting transcripts, validate against Mexican regulations (EMV, LACP, CNBV, NOM standards), iterate with Architect on HLD compliance.
- **Data source:** ChromaDB KB (regulation docs ingested from Google Drive)

### 4.3 Architect Agent
- **Model:** Opus
- **Responsibilities:** High-Level Design, tech stack selection, data model, integration points. Returns to BA if compliance not met.

### 4.4 UX/UI Agent
- **Model:** Sonnet
- **Responsibilities:** Produces a **structured design brief as JSON** (screen layouts, color tokens, typography, component hierarchy, interaction flows). No Figma DevMode required for POC — the coder agent consumes this JSON directly to inform UI code generation. Also generates a lightweight HTML wireframe as a visual artifact for human review.
- **Figma upgrade path:** When Figma Professional is available post-POC, the agent will push/read via the Figma REST API and DevMode for pixel-accurate handoff.

### 4.5 Coder Agent
- **Model:** Sonnet (default) / swap to Haiku for boilerplate / Opus for complex algorithms
- **Responsibilities:** Code generation from architect specs + UX/UI design brief JSON (no Figma dependency for POC), versioning awareness (reads changelog + feature registry), commits to feature branch.

### 4.6 PR Review Agent
- **Model:** Haiku
- **Responsibilities:** Code style, architecture adherence, naming conventions. Runs *before* security scan to filter obvious issues cheaply.

### 4.7 Security Agent
- **Model:** Sonnet
- **Responsibilities:** Orchestrates SAST tools (Semgrep, free OSS), reviews findings against compliance KB, sends fix list to Coder. Max 3 iteration cycles before human escalation.

### 4.8 QA Agent
- **Model:** Sonnet
- **Responsibilities:** Test generation (unit + integration + E2E), coverage validation (>95% prod, >70% internal, skip for POC). Uses pytest / Jest depending on stack.

### 4.9 Infra Agent
- **Model:** Haiku (standard deploys) / Sonnet (novel infra decisions)
- **Responsibilities:** Docker image builds, ECS/Lambda deployment, environment targeting (local / cert / prod), cost-aware resource sizing.

### 4.10 Support / DevOps Agent
- **Model:** Haiku
- **Responsibilities:** CLI installations (gh, aws, android, claude cli), environment bootstrapping, dependency resolution.

### 4.11 Knowledge Base Agent
- **Model:** Haiku
- **Responsibilities:** Answers queries from other agents against ChromaDB (regulations, company standards, past architectural decisions). Triggers re-ingestion when Google Drive signals a change.

---

## 5. Inter-Agent Communication Protocol

All agents communicate via **structured JSON messages** over LangGraph state. No free-text handoffs between agents.

```json
{
  "from_agent": "ba_compliance",
  "to_agent": "architect",
  "message_type": "requirements_ready",
  "ticket_id": "HT-042",
  "payload": {
    "requirements": [...],
    "compliance_gaps": [...],
    "blockers": [],
    "human_approval_required": false
  },
  "timestamp": "2026-06-13T10:00:00Z"
}
```

State is persisted in LangGraph's state store (SQLite for local, DynamoDB on AWS).

---

## 6. Knowledge Base — Google Drive Integration

Mexican regulation documents live in a designated Google Drive folder. The ingestion pipeline keeps ChromaDB in sync.

```
Google Drive folder (regulations/)
    ↓  (Drive API, read-only service account)
Ingestion job (LangChain GoogleDriveLoader)
    ↓  (chunking + embedding via Claude Haiku)
ChromaDB (local container / ECS volume)
    ↓  (similarity search)
BA/Compliance Agent + KB Agent
```

- **Trigger:** Scheduled (daily) + manual trigger via Slack command `/reload-kb`.
- **Credentials:** Google service account JSON stored in `.env` locally, AWS Secrets Manager in cloud.
- **Versioning:** Each doc chunk stores `drive_file_id`, `modified_time`, `version` metadata so stale chunks are replaced, not duplicated.

---

## 7. Slack Integration

Slack serves three purposes: **status broadcasting**, **human-in-the-loop approvals**, and **mobile monitoring**.

### 7.1 Slack App Setup
- Create a free Slack app with: Incoming Webhooks, Interactive Components, Slash Commands.
- One webhook per channel: `#agent-status`, `#human-approvals`, `#deployments`, `#security-alerts`.

### 7.2 Status Messages
Every significant pipeline event posts to `#agent-status`:
```
[HT-042] 🔄 Architect agent → HLD draft ready
[HT-042] ✅ BA compliance check passed
[HT-042] ⏳ Awaiting human approval — HLD review
```

### 7.3 Human-in-the-Loop Approvals (Interactive Messages)
When the orchestrator needs a decision, it posts a Block Kit message with buttons:

```
📋 HLD ready for review — Ticket HT-042
Architect: REST API + PostgreSQL + React Native
Compliance: EMV ✅  LACP ✅  NOM-151 ✅

[✅ Approve]  [🔄 Request Changes]  [❌ Reject]
```

The user can approve from the Slack mobile app. The pipeline is paused (LangGraph `interrupt`) until the response arrives.

### 7.4 Slash Commands
- `/status HT-042` — current pipeline state for a ticket.
- `/reload-kb` — triggers Google Drive re-ingestion.
- `/deploy HT-042 [env]` — triggers infra agent for a specific environment.

---

## 8. Secrets Management

### Local / Dev / POC
```
config/
├── local.properties      # Non-secret local config (ports, paths, flags)
.env                      # Secret values — git-ignored, never committed
.env.example              # Template with placeholder keys only
```
The `SecretsLoader` utility reads from `.env` locally. No AWS dependency required.

### Cert and Prod (AWS — only when deploying beyond local)
- AWS Secrets Manager provisioned at cert/prod promotion time.
- Naming convention: `hypertech/{env}/{service}/{key}` (e.g., `hypertech/prod/anthropic/api_key`).
- `SecretsLoader` detects the environment at startup and switches source automatically: `.env` locally, AWS Secrets Manager in cert/prod.
- Secret rotation handled by AWS natively for supported services.

---

## 9. Scenario Workflows

### Scenario 1 — POC / Demo (Target: ~10 min)
1. Human prompt → Orchestrator classifies as POC
2. Coder agent selects pre-built MVC template (stored in repo)
3. Dummy data populated, screens wired
4. Infra agent runs `docker compose up` locally, app accessible at `localhost`
5. Slack: `#deployments` posts local URL (or ngrok tunnel for shareable link)
6. **No BA, no security scan, no QA** — explicitly skipped for POC

### Scenario 2 — Internal Tool
Full pipeline except: QA coverage threshold is 70%, no client-facing documentation, deployment to local or internal AWS env (cert) depending on audience.

### Scenario 3 — Production / Client
Full pipeline with all agents, 95% QA coverage gate, human approvals at: HLD, Design, Pre-deploy. Deployment to AWS (Lambda or ECS Fargate), gated by GitHub Environment protection rules requiring manual approval.

---

## 10. Feature Registry

`features/feature-registry.json` is updated by the orchestrator on every ticket action:

```json
{
  "HT-042": {
    "title": "Payment flow — EMV compliance",
    "scenario": "production",
    "status": "in_security_review",
    "created": "2026-06-13",
    "last_updated": "2026-06-13",
    "agents_involved": ["ba_compliance", "architect", "coder", "pr_review", "security"],
    "human_approvals": [
      { "stage": "hld", "approved_by": "sinue", "at": "2026-06-13T14:00:00Z" }
    ],
    "branch": "feature/HT-042-payment-emv",
    "changelog_ref": "changelogs/HT-042.md"
  }
}
```

---

## 11. CI/CD Pipeline (GitHub Actions)

```yaml
# Triggers on every feature branch push
on: [push]

jobs:
  pr_review:     # PR Review agent — style + architecture
  security_scan: # Semgrep SAST (free OSS)
  test:          # QA agent test execution + coverage check
  build:         # Docker build
  deploy_dev:    # Auto-deploy to dev env (Lambda)
  deploy_cert:   # Manual approval gate → cert env
  deploy_prod:   # Manual approval gate → prod env
```

Human approval gates use GitHub Environment protection rules (free, native).

---

## 12. Observability Stack

| Signal | Tool | Where |
|---|---|---|
| Agent traces (LLM calls, tool use, latency) | Langfuse (local Docker Compose) | `localhost:3000` for dev/POC; promote to AWS for cert/prod |
| Pipeline status + HitL | Slack `#agent-status` | Mobile + desktop |
| Local container logs | Docker Compose logs | Terminal / Docker Desktop |
| Errors + alerts | Slack `#security-alerts` | Mobile push |
| Token usage / cost tracking | Langfuse + Anthropic console | Dashboard |
| Cert/Prod infra logs | AWS CloudWatch | AWS console (cert/prod only) |

---

## 13. 4-Day Sprint Plan

**Goal:** Working Scenario 1 demo live by Wednesday. Presentation-ready (all 3 scenarios demonstrable) by Thursday EOD.

---

### Monday — Foundation
**End-of-day milestone:** "Hello World" pipeline — orchestrator receives a prompt, classifies it, posts status to Slack.

- [ ] GitHub repo created, branch strategy defined (`main`, `develop`, `feature/*`), GitHub Projects board configured
- [ ] Python project scaffolded: LangGraph installed, core folder structure in place
- [ ] Core state schema defined (`PipelineState` — ticket, scenario, agent outputs, approval status)
- [ ] Inter-agent JSON message protocol finalized
- [ ] Slack app created: Incoming Webhooks, Interactive Components, Slash Commands enabled
- [ ] Channels created: `#agent-status`, `#human-approvals`, `#deployments`, `#security-alerts`
- [ ] `.env.example` + `local.properties` template committed
- [ ] Secrets loader utility (reads `.env` locally; AWS Secrets Manager hook stubbed)
- [ ] Docker Compose with ChromaDB container running locally
- [ ] Langfuse added to local `docker-compose.yml`, API keys in `.env`, traces appearing in dashboard at `localhost:3000`
- [ ] Orchestrator skeleton: receives prompt → classifies scenario → posts "pipeline started" to `#agent-status`

---

### Tuesday — Core Loop (Scenario 1 end-to-end, no deploy)
**End-of-day milestone:** Scenario 1 works — prompt in → code committed to GitHub branch → Slack notified.

- [ ] Orchestrator intent classifier complete (POC / internal / production routing logic)
- [ ] Pre-built MVC templates added to repo (one web, one mobile shell with dummy data and wired screens)
- [ ] Coder agent: selects correct template, populates dummy data, commits to `feature/` branch via GitHub API
- [ ] Feature registry write/read working (`features/feature-registry.json` updated on each pipeline event)
- [ ] Per-ticket changelog file created automatically under `changelogs/`
- [ ] Support/DevOps agent: bootstraps missing CLI tools on first run
- [ ] Slack status broadcasting live from every pipeline transition
- [ ] Langfuse traces verified: full Scenario 1 run visible with latency and token costs

---

### Wednesday — Deploy + Compliance Layer (Demo-Ready)
**End-of-day milestone:** Type a prompt → 10 minutes later, Slack posts a live URL. BA/Compliance agent queryable. ✅ Demo-ready.

- [ ] Infra agent: `docker compose up` locally → app accessible at `localhost` → ngrok tunnel for shareable URL → post to `#deployments`
- [ ] Scenario 1 fully working including local deployment (this is the Friday showstopper)
- [ ] Google Drive service account configured, read access to regulations folder
- [ ] Ingestion pipeline: Drive → ChromaDB (LangChain GoogleDriveLoader + Haiku embeddings)
- [ ] BA/Compliance agent: queries ChromaDB, returns structured compliance report
- [ ] `/reload-kb` Slack slash command triggers manual re-ingestion
- [ ] Slack interactive HitL message working: Approve / Request Changes / Reject buttons functional
- [ ] UX/UI agent: generates design brief JSON + HTML wireframe, posts wireframe link to Slack for review
- [ ] Langfuse dashboard clean: all agents traced, costs visible

---

### Thursday — Full Pipeline Polish + Presentation Prep
**End-of-day milestone:** All 3 scenarios demonstrable (Scenario 1 live, 2 & 3 as guided walkthrough). Presentation script done.

- [ ] Architect agent: HLD output (JSON schema) for Scenario 2/3 demo
- [ ] PR Review agent: code style + architecture check before security
- [ ] Security agent: Semgrep scan integrated, findings posted to Slack and looped back to Coder
- [ ] `/status HT-XXX` Slack command returns current pipeline state
- [ ] GitHub Actions basic pipeline: push to feature branch → PR review → Semgrep → Docker build → deploy dev
- [ ] GitHub Environment protection rules: cert and prod gates require manual approval
- [ ] End-to-end Scenario 3 dry run (even partial walkthrough is enough for Friday)
- [ ] Demo script written: 3 scenarios, key talking points, Langfuse dashboard tour, Slack mobile approval demo
- [ ] Screenshots/screen recording of full flow captured for backup

---

### Friday — Present 🎯
- Live demo: Scenario 1 (full, live deploy in ~10 min)
- Walkthrough: Scenario 2 & 3 pipeline in Langfuse traces
- Show: Slack mobile approval, feature registry, compliance agent output
- Buffer: if anything breaks, fall back to screen recording from Thursday

---

## 14. Confirmed Configuration

All decisions locked — no blockers for Monday.

| Item | Decision |
|---|---|
| Google Drive folder | `19yujC2BeV-Gn61eDyIdBadtDjLmQN9BR` — Google service account setup on Monday |
| AWS region | `us-east-1` — already configured |
| AWS credentials | `aws configure` via CLI on Monday |
| Slack workspace | Existing workspace — create Slack app inside it on Monday |
| GitHub | Personal account `Sinnup`, repo `github.com/Sinnup/hypertech` — use existing |
| Demo URL | ngrok tunnel — no custom domain needed for POC |
| Langfuse | Local Docker Compose — no external account needed |

---

## 15. Out of Scope for POC (Noted for Later)

- **Koog migration (orchestration):** Once the POC validates the concept, migrating the agent orchestration from LangGraph (Python) to Koog (Kotlin-native, JetBrains) is the natural next step. Koog uses coroutines natively, eliminates the Python/Kotlin context switch for teams already on KMP, and will mature rapidly given JetBrains backing. By the time the POC is proven, Koog's multi-agent patterns and community will be more stable. Langfuse stays — its OpenTelemetry integration and Kotlin SDK work equally well with Koog.
- UX/UI agent Figma integration (requires Figma Professional plan; upgrade path documented in §4.4)
- Multi-cloud (AWS only for now)
- QA agent full test suite + 95% coverage gate (skipped for Scenario 1, stubbed for Scenario 3 demo)
- Cost optimization agent
- Client communication agent
- Monitoring/alerting post-deploy agent (CloudWatch alarms as proxy for now)
- Custom mobile app (Slack mobile handles this for POC)
