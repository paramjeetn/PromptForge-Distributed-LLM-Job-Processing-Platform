# PromptForge — Shipping Guide

One file. Open it every day. Check off what's done. Move to the next phase.

**Design detail:** [docs/build_phases/phases.md](docs/build_phases/phases.md)
**API contract:** [docs/spec.md](docs/spec.md)
**Full architecture:** [docs/architecture/complete-architecture.md](docs/architecture/complete-architecture.md)
**Build vs buy:** [docs/build_phases/saas_strategy.md](docs/build_phases/saas_strategy.md)

---

## Phase 1 — Infrastructure ✅

> Provision everything. No application code. Just working cloud resources.

**Done:**
- GCS buckets: `promptforge-input`, `promptforge-output`
- GKE cluster with workload identity
- Firestore `jobs` collection
- Eventarc `OBJECT_FINALIZE` on input bucket → launcher
- IAM service accounts + least-privilege roles
- Artifact Registry for Docker images
- Secret Manager secrets
- All provisioned via Pulumi TypeScript (`infra/`)

---

## Phase 2 — Job Init API ✅

> Clients can submit a job and get back a signed upload URL.

**Done:**
- `shared/models/job.py` — Pydantic `JobRecord`
- `shared/firestore.py` — `create_job()`, `get_job()`, `update_job()`
- `shared/gcs.py` — signed URL generation, stream read/write
- `services/api/` — FastAPI app, `POST /v1/jobs/init`
- Auth: Secret Manager-based API key store (replaced Unkey which shut down in 2025)
  - Keys stored in `promptforge-api-keys` secret as `{"api_key": "client_id"}` JSON
  - 5-minute in-memory cache

---

## Phase 3 — Upload Pipeline ✅

> File upload triggers validation and pod scheduling.

**Done:**
- `services/launcher/` — CloudEvent handler, parses `OBJECT_FINALIZE`
- `validator.py` — stream-validates `prompts.jsonl`, writes invalid lines to `errors.jsonl`
- `spawner.py` — creates GKE Job, injects all env vars
- Per-client concurrency: one active pod, second job goes PENDING

---

## Phase 4 — Execution Engine: Baseline ✅

> Prompts reach the LLM and responses come back.

**Done:**
- `services/execution/loops/dispatch.py` — fixed-rate dispatch via LiteLLM
- `loops/response.py` — 200 → result; 429/5xx → retry; 400/401/403 → errors.jsonl
- Supported providers: OpenAI + Gemini only
- Default RPM/TPM from Tier 1 limits if not specified by user

---

## Phase 5 — Result Buffer ✅

> Batch and flush results instead of one write per response.

**Done:**
- `loops/buffer.py` — flush on 500 responses / 50 MB / 30s
- Writes `results_part_NNN.jsonl`, final flush → `results_final.jsonl`

---

## Phase 6 — Rate Learning ✅

> System discovers real provider limits by itself.

**Done:**
- `rate/controller.py` — slow start, on-429 backoff, congestion avoidance, P95 token window
- `effective_rpm = min(rpm_target, tpm_limit / p95_tokens)`
- Mock LLM provider for local testing

---

## Phase 7 — Checkpointing & Recovery ✅

> Pod crashes become invisible. At-least-once delivery guaranteed.

**Done:**
- `loops/checkpoint.py` — writes `state.json` to GCS every 30s and after every flush
- SIGTERM handler drains in-flight, writes final checkpoint, exits 0
- On startup: restores `offset`, `rpm_target`, `p95_tokens` from `state.json`

---

## Phase 8 — Job Completion & Reconciliation ✅

> Jobs close cleanly. Next queued job starts automatically.

**Done:**
- Completion: EOF + running==0 + retry_queue==[] → final flush → delete input file → COMPLETED
- `job_queue.py` — `maybe_start_next_job()` promotes oldest PENDING job for same client
- Note: `queue.py` is a stdlib passthrough (prevents shadowing Python's `queue` module)

---

## Phase 9 — Status & Results API ✅

> Clients can poll progress, download results, and cancel jobs.

**Done:**
- `GET /v1/jobs/{job_id}` — status + progress
- `GET /v1/jobs/{job_id}/results` — signed URLs for result files
- `DELETE /v1/jobs/{job_id}` — cancellation

---

## Phase 10 — Observability + Deploy ✅

> Full visibility. Deployed to production.

**Done:**
- Sentry SDK in all 3 services
- OTel/OTLP → Axiom across all services (`shared/observability.py`)
- All 3 services deployed: API + Launcher on Cloud Run, Execution on GKE
- Docker images built for `linux/amd64` and pushed to Artifact Registry
- Secret Manager holds API keys (JSON dict format)
- Test API key issued → client `e2e-test-client` (key in Secret Manager)

---

## Release Checklist

- [x] All 10 phases complete
- [x] Images built and pushed to Artifact Registry
- [x] Cloud Run services deployed (api, launcher)
- [x] GKE execution image updated
- [x] API key in Secret Manager (`promptforge-api-keys`)
- [x] Observability: Sentry + Axiom wired up
- [ ] E2E test passing end-to-end against live system
- [ ] `.env.example` matches all env vars actually used in code
- [ ] `make lint` passes
- [ ] Better Stack status page live

---

## Current Phase

**→ Post-Phase 10 — E2E Validation**

**Live API:** `https://promptforge-api-517402593902.us-central1.run.app`
**Test client:** `e2e-test-client` (key in Secret Manager `promptforge-api-keys`)

**Next action:** Run `pytest tests/e2e/test_e2e.py -v -s` — this is the final gate before v1.0.

**Known fixes applied:**
- Stale QUEUED jobs cleared (Firestore) — `03e63834` and `82dfa040` marked FAILED
- Circular import in execution pod fixed (`queue.py` → stdlib passthrough, logic moved to `job_queue.py`)
- Unkey replaced with Secret Manager key store
- Docker images rebuilt for `linux/amd64`
