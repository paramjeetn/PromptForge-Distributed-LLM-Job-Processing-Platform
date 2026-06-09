# PromptForge — Build Progress Report

**Date:** 2026-06-09
**Status:** 2 of 10 phases complete
**GCP Project:** `promptforge-1212` | **Region:** `us-central1`

---

## Live GCP Resources

All provisioned via Pulumi (`infra/`). Stack: `dev`, local state, 18 resources.

| Resource | Name | Status |
|----------|------|--------|
| GKE Autopilot Cluster | `promptforge-cluster` | RUNNING |
| GCS Input Bucket | `promptforge-input-promptforge-1212` | Created |
| GCS Output Bucket | `promptforge-output-promptforge-1212` | Created |
| Firestore Database | `(default)` — Native mode, us-central1 | Created |
| Service Account — API | `promptforge-api@promptforge-1212.iam.gserviceaccount.com` | Created |
| Service Account — Launcher | `promptforge-launcher@promptforge-1212.iam.gserviceaccount.com` | Created |
| Service Account — Exec Pod | `promptforge-exec@promptforge-1212.iam.gserviceaccount.com` | Created |
| Workload Identity Binding | Exec K8s SA → GCP exec SA | Created |
| IAM Role Bindings | 10 bindings across 3 service accounts | Created |

**Cluster endpoint:** `34.31.83.10`

---

## Phase 1 — Infrastructure

**Status: COMPLETE**

### Files written

| File | What it creates |
|------|----------------|
| `infra/Pulumi.yaml` | Pulumi project config |
| `infra/package.json` + `tsconfig.json` | TypeScript project setup |
| `infra/index.ts` | Stack entry, imports all modules, exports outputs |
| `infra/gcs.ts` | Two GCS buckets (input + output) |
| `infra/gke.ts` | GKE Autopilot cluster + Workload Identity binding |
| `infra/firestore.ts` | Firestore Native database |
| `infra/iam.ts` | Three service accounts + 10 IAM role bindings |
| `infra/eventarc.ts` | Eventarc trigger (code ready, applied in Phase 3) |

### Issues resolved

**Issue 1 — Workload Identity race condition**
The `exec-workload-identity` IAM binding failed on the first `pulumi up` with:
> `Identity Pool does not exist (promptforge-1212.svc.id.goog)`

Cause: the IAM binding ran in parallel with the GKE cluster creation. The identity pool only exists after the cluster is fully up.

Fix: moved the binding from `iam.ts` to `gke.ts` and added `dependsOn: [cluster]`. Second `pulumi up` succeeded in 26s.

**Issue 2 — gke-gcloud-auth-plugin not in PATH on Windows**
After `gcloud components update`, the binary existed in the component registry but not on disk. `kubectl get nodes` failed.

Fix: verified cluster status via `gcloud container clusters describe` instead. Cluster confirmed RUNNING.

---

## Phase 2 — Job Init API

**Status: COMPLETE** — 5/5 unit tests passing

### Files written

**Shared library (`shared/`):**

| File | Contents |
|------|----------|
| `__init__.py` | Module marker |
| `models/__init__.py` | Module marker |
| `models/job.py` | `JobRecord` Pydantic model, `JobStatus` enum (7 statuses) |
| `models/prompt.py` | `PromptSchema` — `prompt_id: int`, `prompt: str` |
| `firestore.py` | `create_job()`, `get_job()`, `update_job()`, `get_oldest_pending_job()` |
| `gcs.py` | `generate_signed_upload_url()`, `stream_read()`, `write_bytes()`, `delete_blob()` |
| `otel.py` | No-op tracer/meter stubs — wired up in Phase 10 |

**API service (`services/api/`):**

| File | Contents |
|------|----------|
| `requirements.txt` | fastapi, pydantic, google-cloud-*, httpx, sentry-sdk, python-dotenv |
| `Dockerfile` | Python 3.12-slim, copies shared/ first, uvicorn entry point |
| `main.py` | FastAPI app, Sentry init, mounts `/v1` router, `/healthz` endpoint |
| `middleware/auth.py` | `verify_api_key()` — calls Unkey API, returns `client_id` from `ownerId` |
| `routes/jobs.py` | `POST /v1/jobs/init` — validates provider, creates JobRecord, returns 202 |

**Tests:**

| File | Tests | Result |
|------|-------|--------|
| `tests/unit/api/test_init.py` | 5 tests | All passing |

Tests cover: 202 response code, response shape (`job_id`/`upload_url`/`expires_at`), invalid provider → 422, missing provider → 422, optional fields (`rpm`/`tpm`/`max_retries`).

### Issues resolved

**Issue — `patch()` doesn't work for FastAPI `Depends()`**
The test used `patch("middleware.auth.verify_api_key", ...)` but FastAPI had already resolved the dependency reference. The real function ran, made a network call to Unkey, and failed with `ConnectError`.

Fix: replaced `patch()` with `app.dependency_overrides[verify_api_key] = lambda: MOCK_CLIENT_ID`. FastAPI's dependency injection system respects `dependency_overrides` at request time.

---

## Phases 3–10 — Pending

| Phase | What it builds | Key files | Status |
|-------|---------------|-----------|--------|
| 3 — Upload Pipeline | Launcher: validates JSONL, creates GKE Job | `services/launcher/main.py`, `validator.py`, `spawner.py` | Pending |
| 4 — Execution Baseline | Dispatch loop, response handler, LLM calls via LiteLLM | `services/execution/main.py`, `loops/dispatch.py`, `loops/response.py` | Pending |
| 5 — Result Buffer | Batch GCS writes (500 responses / 50MB / 30s) | `services/execution/loops/buffer.py` | Pending |
| 6 — Rate Learning | Slow start → congestion avoidance (TCP-style) | `services/execution/rate/controller.py` | Pending |
| 7 — Checkpointing | `state.json` written every 30s, crash recovery | `services/execution/loops/checkpoint.py` | Pending |
| 8 — Job Completion | Reconciliation, chain trigger, integration tests | `services/execution/main.py` update, `tests/integration/test_full_job.py` | Pending |
| 9 — Status & Results API | `GET /v1/jobs/{id}`, `GET /v1/jobs/{id}/results`, `DELETE` | `services/api/routes/jobs.py` update | Pending |
| 10 — Observability | Real OTel → Axiom, Sentry, Better Stack | `shared/otel.py` + service updates | Pending |

---

## Key Decisions Made

**GKE Autopilot over Standard GKE**
Pay per pod resource request, not per idle node. Execution pods are ephemeral — one per job, running only while processing. Autopilot avoids paying for empty nodes between jobs.

**Local Pulumi state over Pulumi Cloud**
Free, no account required, no limits for a solo project. State stored at `~/.pulumi/`. Can migrate to Pulumi Cloud with one command later.

**uv for local dev, Docker for container builds**
`uv` manages the Python venv for running tests locally (`make test`). Docker builds the actual container images that deploy to GCP. They complement each other — the Dockerfiles use standard pip inside the container.

**OTel no-op stub in `shared/otel.py`**
All services import `get_tracer()` and `get_meter()` from `shared/otel.py`. The stubs return no-op objects — no OTel packages required in Phases 2–9. Phase 10 replaces the internals of this one file with real OTLP exporters. Zero changes needed in service code.

**`app.dependency_overrides` for FastAPI unit tests**
FastAPI resolves `Depends()` by calling the actual function object at request time. Standard `unittest.mock.patch()` cannot intercept this. The correct pattern is `app.dependency_overrides[verify_api_key] = lambda: MOCK_CLIENT_ID` — FastAPI checks this dict before calling the real dependency.

---

## Toolchain

| Tool | Version / Detail |
|------|-----------------|
| Python | 3.12.11 (uv venv at `.venv/`) |
| Pulumi | gcp-typescript template, `@pulumi/gcp ^9.0.0` |
| GCP CLI | 571.0.0 |
| pytest | 9.0.3 |
| FastAPI | 0.115+ |
| GCP Account | Free trial, project `promptforge-1212` |

---

## Next Action

**Phase 3 — Upload Pipeline**

When a client uploads `prompts.jsonl` to GCS, the `OBJECT_FINALIZE` event triggers the Launcher service. Files to write next:

```
services/launcher/main.py      — CloudEvent handler entry point
services/launcher/validator.py — stream-validate JSONL line by line (Pydantic)
services/launcher/spawner.py   — create GKE Job via K8s API
tests/unit/launcher/test_validator.py
```
