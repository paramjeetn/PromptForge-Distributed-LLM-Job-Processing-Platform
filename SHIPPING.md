# PromptForge — Shipping Guide

One file. Open it every day. Check off what's done. Move to the next phase.

**Design detail:** [docs/build_phases/phases.md](docs/build_phases/phases.md)
**API contract:** [docs/spec.md](docs/spec.md)
**Full architecture:** [docs/architecture/complete-architecture.md](docs/architecture/complete-architecture.md)
**Build vs buy:** [docs/build_phases/saas_strategy.md](docs/build_phases/saas_strategy.md)

---

## Phase 1 — Infrastructure

> Provision everything. No application code. Just working cloud resources.
> Detail: [phases.md → Phase 1](docs/build_phases/phases.md)

**Files to create:**
- [ ] `infra/Pulumi.yaml`
- [ ] `infra/package.json` + `infra/tsconfig.json`
- [ ] `infra/index.ts` — stack entry, imports all resources
- [ ] `infra/gcs.ts` — `promptforge-input` + `promptforge-output` buckets
- [ ] `infra/gke.ts` — cluster, node pool, workload identity
- [ ] `infra/firestore.ts` — `jobs` collection + security rules
- [ ] `infra/eventarc.ts` — `OBJECT_FINALIZE` on input bucket → launcher
- [ ] `infra/iam.ts` — service accounts + least-privilege roles per service

**Done when:**
- `make infra-up` succeeds with no errors
- `kubectl get nodes` returns a healthy node
- Write a Firestore document and read it back
- Upload a file to `promptforge-input` and confirm Eventarc trigger fires in Cloud Logging
- `gcloud secrets versions access latest --secret=test` returns a value

---

## Phase 2 — Job Init API

> Clients can submit a job and get back a signed upload URL. Nothing executes yet.
> Detail: [phases.md → Phase 2](docs/build_phases/phases.md)

**Files to create:**
- [ ] `shared/__init__.py`
- [ ] `shared/models/__init__.py`
- [ ] `shared/models/job.py` — Pydantic `JobRecord` matching Firestore schema
- [ ] `shared/models/prompt.py` — `PromptSchema`: `{ prompt_id: int, prompt: str }`
- [ ] `shared/firestore.py` — `create_job()`, `get_job()`, `update_job()` helpers
- [ ] `shared/gcs.py` — `generate_signed_upload_url()`, `stream_read()`, `write_bytes()`
- [ ] `shared/otel.py` — stub: initialise tracer + meter + logger (Axiom config in Phase 10)
- [ ] `services/api/requirements.txt`
- [ ] `services/api/Dockerfile`
- [ ] `services/api/main.py` — FastAPI app, mounts router, calls `sentry_sdk.init()`
- [ ] `services/api/middleware/auth.py` — Unkey `keys.verify()` → resolve `client_id`
- [ ] `services/api/routes/jobs.py` — `POST /v1/jobs/init` only (other endpoints in Phase 9)
- [ ] `tests/unit/api/test_init.py`

**Done when:**
- `POST /v1/jobs/init` with valid Unkey key → 202, `{ job_id, upload_url, expires_at }`
- Firestore document exists at `/jobs/{job_id}` with `status = AWAITING_UPLOAD`
- `PUT <upload_url>` with a test file → file lands in GCS
- Missing `provider` → 400
- Invalid/revoked API key → 401

---

## Phase 3 — Upload Pipeline

> A file upload triggers validation and pod scheduling. End of the event-driven ingestion path.
> Detail: [phases.md → Phase 3](docs/build_phases/phases.md)

**Files to create:**
- [ ] `services/launcher/requirements.txt`
- [ ] `services/launcher/Dockerfile`
- [ ] `services/launcher/main.py` — CloudEvent handler entry, parses `OBJECT_FINALIZE`
- [ ] `services/launcher/validator.py` — stream-validate `prompts.jsonl` line by line (Pydantic), write invalid lines to `errors.jsonl`
- [ ] `services/launcher/spawner.py` — GKE Job creation, inject all env vars
- [ ] `tests/unit/launcher/test_validator.py`

**Done when:**
- Upload valid `prompts.jsonl` → Firestore `status = QUEUED` within seconds → GKE pod scheduled (`kubectl get pods`)
- File with invalid lines → valid lines proceed, invalid lines in `errors.jsonl`
- Second job submitted while first is `QUEUED` → second job `status = PENDING`, no pod created
- Completely non-JSONL file → `status = FAILED`

---

## Phase 4 — Execution Engine: Baseline

> Prompts reach the LLM and responses come back. No rate learning, buffering, or checkpointing yet.
> Detail: [phases.md → Phase 4](docs/build_phases/phases.md)

**Files to create:**
- [ ] `services/execution/requirements.txt` — includes `litellm`
- [ ] `services/execution/Dockerfile`
- [ ] `services/execution/main.py` — read env vars, fetch `api_key` from Firestore, init state, run loops
- [ ] `services/execution/loops/__init__.py`
- [ ] `services/execution/loops/dispatch.py` — fixed-rate dispatch (`interval = 60 / RPM_LIMIT`), priority: retry queue first, GCS byte-range stream, `litellm.acompletion()`
- [ ] `services/execution/loops/response.py` — 200 → write result direct to GCS; 429/5xx → retry queue; 400/401/403 → errors.jsonl
- [ ] `tests/unit/execution/test_dispatch.py`

**Done when:**
- Submit 20 prompts → all responses land in GCS → Firestore `status = COMPLETED`
- Bad `prompt_id` format → lands in `errors.jsonl`
- Pod memory stays flat across file sizes (verify with `kubectl top pod`)
- Kill pod mid-run → job stalls (expected — recovery comes in Phase 7)

---

## Phase 5 — Result Buffer

> Stop writing one file per response. Batch and flush instead.
> Detail: [phases.md → Phase 5](docs/build_phases/phases.md)

**Files to create / modify:**
- [ ] `services/execution/loops/buffer.py` — `result_buffer`, `error_buffer`; flush on 500 responses / 50 MB / 30s; write `results_part_NNN.jsonl`; final flush writes `results_final.jsonl`
- [ ] Update `loops/response.py` — append to buffer instead of direct GCS write

**Done when:**
- 1,200 prompts → exactly 3 part files (`part_001`: 500, `part_002`: 500, `final`: 200)
- GCS write count is ~3, not 1,200
- 30s flush fires on time with a partial buffer
- Error prompts in `errors.jsonl` with `prompt_id`, `attempts`, `last_error_code`

---

## Phase 6 — Rate Learning

> The system discovers real provider limits by itself. No manual RPM/TPM tuning.
> Detail: [phases.md → Phase 6](docs/build_phases/phases.md)

**Files to create / modify:**
- [ ] `services/execution/rate/__init__.py`
- [ ] `services/execution/rate/controller.py` — `RateController`: slow start (`rpm *= 1.5` / 30s), on-429 backoff (`rpm *= 0.75` + 60s cooldown), congestion avoidance (`rpm += 1` / 30s), P95 token rolling window (last 1000 samples), `effective_rpm = min(rpm_target, tpm_limit / p95_tokens)`
- [ ] Update `loops/dispatch.py` — replace fixed rate with `RateController.effective_rpm`
- [ ] `tests/mocks/llm_provider.py` — FastAPI mock: accepts LiteLLM-compatible requests, fires 429s when requests exceed configurable RPM ceiling
- [ ] `tests/unit/execution/rate/test_controller.py` — test slow start progression, backoff math, P95 calculation, TPM ceiling enforcement

**Done when:**
- Job against mock provider (RPM=100) → `rpm_target` converges to ~100 without being told
- 429 at `rpm_target=500` → drops to 375, cooldown fires, dispatch resumes
- Prompts with longer responses → `p95_tokens` rises → `effective_rpm` drops automatically
- `rpm_target` never exceeds user-supplied `RPM_LIMIT`

---

## Phase 7 — Checkpointing & Recovery

> Pod crashes become invisible. At-least-once delivery guaranteed.
> Detail: [phases.md → Phase 7](docs/build_phases/phases.md)

**Files to create / modify:**
- [ ] `services/execution/loops/checkpoint.py` — write `state.json` to GCS every 30s and after every buffer flush; SIGTERM handler sets `dispatch_enabled = False`
- [ ] Update `services/execution/main.py` — on startup, check GCS for `state.json`; if found, restore `offset`, `rpm_target`, `tpm_target`, `p95_tokens`; log `checkpoint_restored`

**Done when:**
- Kill pod at `offset=50000` → new pod starts → reads `state.json` → resumes from ~last checkpoint offset
- Some prompts near boundary appear twice in results (at-least-once — expected)
- `SIGTERM` → dispatch stops, in-flight drains, final checkpoint written, pod exits code 0
- Cold start (no `state.json`) works correctly

---

## Phase 8 — Job Completion & Reconciliation

> Jobs close cleanly. Nothing is silently lost. Next queued job starts automatically.
> Detail: [phases.md → Phase 8](docs/build_phases/phases.md)

**Files to modify:**
- [ ] Update `services/execution/main.py` — completion condition (`offset == EOF AND running == 0 AND retry_queue == []`); final flush sequence; bitset reconciliation pass; delete `prompts.jsonl` from input bucket; Firestore `status = COMPLETED`; trigger next `PENDING` job for same `client_id`
- [ ] `tests/integration/test_full_job.py` — submit job → poll status → download results → verify prompt count

**Done when:**
- Full job → `status = COMPLETED`, all result files present, input file deleted
- Synthetic missing `prompt_id` → logged as WARNING in structured logs
- Queue two jobs → first completes → second starts within seconds automatically
- Bitset memory stays flat for large jobs

---

## Phase 9 — Status & Results API

> Clients can poll progress, download results, and cancel jobs.
> Detail: [phases.md → Phase 9](docs/build_phases/phases.md)

**Files to modify:**
- [ ] Update `services/api/routes/jobs.py` — add `GET /v1/jobs/{job_id}`, `GET /v1/jobs/{job_id}/results`, `DELETE /v1/jobs/{job_id}`
- [ ] `tests/unit/api/test_status.py`
- [ ] `tests/unit/api/test_results.py`

**Done when:**
- Poll `GET /jobs/{id}` during execution → `progress_pct` climbs in real time
- `GET /jobs/{id}/results` on completed job → signed URLs → download → merge JSONL → prompt count matches
- `GET /jobs/{id}/results` on running job → 409
- `DELETE /jobs/{id}` on running job → Firestore flips to `CANCELLED` → pod stops dispatching
- `GET /jobs/{id}` with wrong API key → 404 (not 403 — do not reveal existence)

---

## Phase 10 — Observability

> Full visibility. Every prompt traced. Every rate event metricked. Errors captured.
> Detail: [phases.md → Phase 10](docs/build_phases/phases.md)

**Files to modify:**
- [ ] Update `shared/otel.py` — full OTel setup: OTLP exporter to Axiom (`OTEL_EXPORTER_OTLP_ENDPOINT`), resource attributes, tracer + meter + logger
- [ ] Update `services/api/main.py` — `sentry_sdk.init(DSN, before_send=strip_prompt_content)`
- [ ] Update `services/launcher/main.py` — `sentry_sdk.init()`
- [ ] Update `services/execution/main.py` — `sentry_sdk.init()`; add OTel spans to dispatch loop, response handler, buffer flush, checkpoint write
- [ ] Add key metrics to `loops/dispatch.py` — `rate.rpm_target`, `rate.effective_rpm`, `queue.running`, `queue.retry_depth`
- [ ] Add key metrics to `loops/response.py` — `prompts.completed`, `prompts.failed`, `rate.429_events`, `llm.latency_ms`
- [ ] Add key metrics to `loops/buffer.py` — `buffer.size`
- [ ] Add key metrics to `rate/controller.py` — `rate.p95_tokens`, `rate.cooldown_active`
- [ ] Configure 3 Better Stack monitors: API `/healthz`, Launcher health, GKE node

**Done when:**
- Submit job → open Axiom → `rate.rpm_target` gauge climbs in real time
- Find `prompt_id` in Axiom trace → see all retry attempts as child spans
- Kill pod with unhandled exception → error appears in Sentry with stack trace
- Take API offline → Better Stack fires alert within 3 minutes
- Confirm no `prompt_id` label on any metric (cardinality check)

---

## Release Checklist

Before calling it v1.0:

- [ ] All 10 phases checked off above
- [ ] `make test` passes (unit + integration)
- [ ] `make lint` passes
- [ ] `.env.example` matches all env vars actually used in code
- [ ] All three Dockerfiles build cleanly: `make build`
- [ ] `SENTRY_DSN` and `AXIOM_TOKEN` set in all GCP service configs
- [ ] Unkey API created, first client key issued
- [ ] Better Stack status page live and linked from README

---

## Current Phase

**→ Phase 1 — Infrastructure**

Next action: `cd infra && pulumi new gcp-typescript`
