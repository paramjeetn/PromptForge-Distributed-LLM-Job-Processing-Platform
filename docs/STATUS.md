# PromptForge — Status
_Last updated: 2026-06-11_

---

## What's Working (End-to-End Verified)

**Phase 1 — Infra**
GKE Autopilot cluster, GCS buckets, Firestore, Secret Manager, Service Accounts — all live on GCP (`promptforge-1212`, `us-central1`).

**Phase 2 — Job Init API**
`POST /v1/jobs/init` creates Firestore doc + returns signed GCS upload URL. 11 integration tests pass against real GCP.

**Phase 3 — Launcher**
GCS upload → Eventarc → Launcher validates JSONL → spawns GKE Job → Firestore `QUEUED`. 11 integration tests pass against real GCP.

**Phase 4 — Execution Pod**
GKE pod streams prompts from GCS → calls LLM (OpenAI gpt-4o-mini) at configured RPM via LiteLLM → writes results_part_NNN.jsonl + results_final.jsonl → Firestore `COMPLETED`. 10/10 prompts completed end-to-end.

**Phase 5–9 — Core Features**
- Dispatch loop (LiteLLM → OpenAI/Gemini) — implemented
- Result buffering (flush on 500 responses / 50 MB / 30s) — implemented
- Rate learning (slow-start, 429 backoff, P95 token window) — implemented
- Checkpointing (state.json every 30s, SIGTERM drain) — implemented
- Job completion + next-job promotion (`job_queue.py`) — implemented

**Observability**
- Sentry wired up in all 3 services
- OTel/OTLP → Axiom in all 3 services

---

## Infrastructure (GCP)

| Resource | State |
|----------|-------|
| GCS buckets `promptforge-input` / `promptforge-output` | Live |
| GKE cluster | Running, workload identity configured |
| Firestore `jobs` collection | Reads/writes confirmed |
| Eventarc `OBJECT_FINALIZE` trigger → Launcher | Wired up |
| Artifact Registry | Images pushed and pullable |
| Secret Manager | API keys stored and accessible |
| IAM / service accounts | Least-privilege, no permission errors |

**Manual GCP setup (not yet in Pulumi):**
- Cloud Router + Cloud NAT (`promptforge-router` / `promptforge-nat`, us-central1)
- Kubernetes ServiceAccount `promptforge-exec` with Workload Identity annotation
- Eventarc trigger `promptforge-gcs-trigger`
- Cloud Run service `promptforge-launcher`

---

## Bugs Fixed

| Bug | Root Cause | Fix |
|-----|-----------|-----|
| 401 "Invalid API key" | `.dockerignore` missing → stale `auth.cpython-312.pyc` baked into image | Created `.dockerignore` excluding `**/__pycache__`, rebuilt API image |
| 500 "JSON parse error" on auth | `promptforge-api-keys` secret had malformed JSON (Windows `echo` ate quotes) | Recreated via Python `json.dump`, now version 3 |
| Execution pod CrashLoopBackOff | `queue.py` shadowed stdlib `queue` module → circular import in urllib3 | Deleted `queue.py` — logic already in `job_queue.py` |
| Stale QUEUED jobs blocking submissions | Crashed pods left jobs in non-terminal states | `fix_stale_job.py` marks all active-state jobs as FAILED |
| `docker-credential-gcloud` not in PATH | Docker Desktop could not find gcloud credential helper | Used `gcloud auth print-access-token \| docker login` for manual auth |

---

## What's Left

| Item | Status |
|------|--------|
| Fix Pulumi | Cloud NAT, k8s ServiceAccount, Eventarc trigger manually created — not in infra code yet |
| Results buffering validation | Coded. Needs validation with larger batches (500-record/50MB/30s flush) |
| Rate learning validation | Coded. Needs real-world validation with larger prompt sets |
| Cold start optimization | GKE pod cold start adds ~3–9 min to first job |

---

## Quick Reference

| Item | Value |
|------|-------|
| Live API URL | `https://promptforge-api-517402593902.us-central1.run.app` |
| Test API key | Stored in Secret Manager `promptforge-api-keys` |
| Test client ID | `e2e-test-client` |
| GKE cluster | `promptforge-cluster` (us-central1) |
| Execution image | `us-central1-docker.pkg.dev/promptforge-1212/promptforge/execution:latest` |
| GCP project | `promptforge-1212` |
