# HT-09FBB3 — ChromaDB / Drive KB ingestion robustness

- **Scenario**: fix
- **Agents**: ba_compliance, knowledge_base (consumers)
- **Branch**: feature/HT-09FBB3 (stacked on feature/HT-CD1878)
- **Created**: 2026-06-25 19:30 UTC
- **Status**: in_progress

## Summary
ChromaDB read as "empty" despite files in the Drive folder. Root cause:
`GOOGLE_SERVICE_ACCOUNT_JSON` is the placeholder `path/to/service-account.json`,
so private-folder ingestion silently fell back to public `gdown` (which can't
read an org/private folder), leaving the collection empty — and nothing
distinguished "empty" from "server down".

## Changes
- **core/memory/chroma.py**: added `health()` (reachable + count + collection,
  never raises) and `count()`; `query()` now catches connection errors and an
  empty collection, returning `[]` with a logged reason instead of crashing the
  pipeline. Cached client/collection reset on failure so transient outages
  recover.
- **ingestion/google_drive/ingest.py**: detect the placeholder service-account
  path (`_is_placeholder_sa`) and warn clearly; the "0 files downloaded" error
  now gives actionable next steps (configure SA, share publicly, or seed local).
  Extracted `_chunk_and_upsert()` and added `seed_local()` + a `--seed-local`
  CLI flag to populate the KB from bundled sample docs without Drive.
- **ingestion/sample_docs/** (new): EMV, CNBV/MX, and LACP/data-protection
  snippets for offline KB seeding (demos/tests).

## Verification
- `python -m ingestion.google_drive.ingest --seed-local` → 8 chunks ingested;
  `chroma.health()` → `{reachable: True, count: 8}`; RAG query returns EMV hits.
- `uv run pytest tests/e2e/test_chroma_kb.py` → 3 passed (placeholder detection,
  unreachable-query returns [], health reports unreachable).
- **Still needs user**: verifying ingestion against the *real* Drive folder
  requires a valid `GOOGLE_SERVICE_ACCOUNT_JSON` key (or a public-shared folder).

## Commits
- (pending)
