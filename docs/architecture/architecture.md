# PromptForge Architecture

## Overview

PromptForge is a cloud-native LLM job execution platform built on GCP. A client submits a job — an API key, a model, and a list of prompts — and PromptForge executes every prompt against the provider, respects rate limits, handles failures, and returns results. The client does not manage concurrency, quota, or retries.

---

## Core Design Principles

- **One API key = one client.** All quota, scheduling, and isolation is per API key.
- **Prompts are the unit of work.** Not batches. Every prompt is independently tracked, scheduled, retried, and completed.
- **Quota is the scheduling primitive.** The scheduler never dispatches more than the available RPM and TPM budget allows.
- **Intelligence in the scheduler, not the workers.** Workers are stateless executors. All decisions live in the scheduler.

---

## System Architecture

```
Client
  │
  ▼
Cloud Run — API Layer
  │
  ├──► GCS — store original request (immutable)
  └──► Firestore — create job + prompt records
            │
            ▼
       GKE Pod — Scheduler (always-on, one per platform)
            │
            ├── reads quota state from Firestore
            ├── runs dispatch loop per active client
            └── enqueues tasks to Cloud Tasks
                        │
                        ▼
              Cloud Run — Workers (autoscaling, stateless)
                        │
                        ▼
              LLM Provider (OpenAI / Gemini / Anthropic)
                        │
                   ┌────┴────┐
                   ▼         ▼
                 GCS       Firestore
              (results)   (state update)
```

---

## Components

### API Layer — Cloud Run

Accepts job submissions from clients. On every submission:

1. Hashes the API key to derive a stable `client_id` (raw key never stored)
2. Generates a `job_id`
3. Writes the original request to GCS at `jobs/{client_id}/{job_id}/request.json`
4. Creates one Firestore prompt record per prompt, all initially `PENDING`
5. Creates a Firestore job record with status `QUEUED` and counters `{total, pending, running, completed, failed}`
6. Returns `job_id` immediately — processing is fully asynchronous

Also exposes:
- `GET /jobs/{job_id}` — returns current job status and counters
- `DELETE /jobs/{job_id}` — cancels a running job

---

### Scheduler — GKE Pod

A single always-on GKE pod. This is the brain of the platform. It runs one concurrent dispatch loop per active client and is responsible for all quota decisions.

**Why GKE and not Cloud Run:**
The scheduler is a long-running stateful loop, not a request-response service. Jobs can run for hours or days. GKE gives it a persistent runtime with no timeout constraints, no re-trigger complexity, and no cold start overhead. A single small pod (2 vCPU / 512MB) is sufficient — the scheduler is I/O bound, mostly sleeping between iterations.

**Scheduler lifecycle:**

1. On startup, queries Firestore for any clients with `pending > 0` (recovers any work in progress from a restart)
2. Watches Firestore for new jobs in real time
3. Spawns a dispatch loop coroutine per active client
4. Exits a client's loop when `pending + running == 0`

**Dispatch loop — one iteration per client:**

1. **Cancellation check** — if job status is `CANCELLED`, skip all its pending prompts and continue
2. **Stale recovery** — query for prompts with `status = RUNNING` and `running_since > 120s`. Reset to `PENDING`. These are workers that crashed without reporting back.
3. **Cooldown check** — if `now < cooldown_until` on the quota document, sleep until expiry
4. **Window reset** — if current window is older than 60 seconds, reset `rpm_used` and `tpm_used` to zero
5. **Quota calculation:**
   ```
   available_rpm = rpm_limit - rpm_used
   available_tpm = tpm_limit - tpm_used
   n = min(available_rpm, floor(available_tpm / p95_tokens), MAX_BATCH)
   ```
6. **Fetch** — pull `n` `PENDING` prompts from Firestore (FIFO across all jobs for this client)
7. **Atomic write** — in a single Firestore batch: mark all fetched prompts `RUNNING`, set `running_since`, increment `rpm_used` and `tpm_used`
8. **Enqueue** — create one Cloud Tasks task per prompt on the client's queue
9. **Sleep 1 second** — prevents tight-loop polling under low-quota conditions

---

### Cloud Tasks — Per-Client Queues

One Cloud Tasks queue per client (`queue-{client_id}`). Used purely as a durable delivery mechanism between the scheduler and workers.

**Key configuration decisions:**
- **Native retry is disabled.** All retry logic lives in the worker. Cloud Tasks retrying independently would fire against an empty quota budget and cause 429 cascades.
- **Per-client queues** allow pausing delivery for a specific client during cooldown without affecting others.

---

### Workers — Cloud Run (Autoscaling)

Stateless Cloud Run instances. Each invocation handles exactly one prompt.

**Worker lifecycle:**

1. Receive task payload from Cloud Tasks
2. Retrieve the LLM provider API key from Secret Manager
3. Execute the LLM API call
4. On **success:**
   - Write result to GCS at `results/{job_id}/part_{prompt_id}.jsonl`
   - Mark prompt `COMPLETED` in Firestore
   - Update job counters atomically (`completed++`, `running--`)
   - Write actual token usage to quota document for P95 learning
5. On **429:**
   - Return prompt to `PENDING` (no retry increment)
   - Set `cooldown_until = now + 60s` on quota document
   - Pause the client's Cloud Tasks queue
6. On **transient error (5xx, timeout):**
   - Increment `retry_count`
   - If `retry_count < max_retries`: return to `PENDING`
   - If `retry_count >= max_retries`: mark `FAILED`, write error to GCS
7. On **terminal error (4xx except 429):**
   - Mark `FAILED` immediately, no retry
8. Emit OpenTelemetry span with full metadata
9. Return HTTP 200 to acknowledge Cloud Tasks delivery

Workers have no knowledge of quota, other prompts, or other workers.

---

## Data Model

### Firestore

```
jobs/{job_id}
  ├── client_id, job_id, provider, model
  ├── status          QUEUED | RUNNING | COMPLETED | FAILED | CANCELLED
  ├── total, pending, running, completed, failed
  ├── max_retries
  └── created_at, started_at, completed_at

prompts/{job_id}/{prompt_id}
  ├── prompt_id, job_id, client_id
  ├── prompt_text
  ├── status          PENDING | RUNNING | COMPLETED | FAILED
  ├── retry_count
  ├── running_since
  ├── estimated_tokens, actual_input_tokens, actual_output_tokens
  └── error_message

quota/{client_id}
  ├── rpm_limit, tpm_limit        effective limits (min of provider default and client cap)
  ├── rpm_used, tpm_used          consumed in current window
  ├── window_start                start of current 60s window
  ├── p95_output_tokens           rolling P95 estimate, per client + model
  ├── cooldown_until
  └── last_429_at

model_defaults/{provider}/{model}
  ├── tier1_rpm, tier1_tpm        provider's published tier-1 limits
  └── p95_seed_tokens             conservative cold-start estimate
```

### GCS

```
gs://{bucket}/
  ├── jobs/{client_id}/{job_id}/request.json
  ├── results/{job_id}/part_{prompt_id}.jsonl
  └── errors/{job_id}/error_{prompt_id}.json
```

---

## Quota Model

### Dual constraint

Every dispatch decision is gated by two dimensions simultaneously — a prompt is only dispatched when both have available budget:

- **RPM** — requests per minute. Hard ceiling set by the provider.
- **TPM** — tokens per minute. Requires estimating token cost before the response is seen.

### Token estimation

```
estimated_tokens = input_tokens (exact, from tokenizer) + p95_output_tokens (learned)
```

P95 is maintained per `client_id × model` and updated after every successful completion using the actual output token count. On cold start (no history), the system uses `p95_seed_tokens` from the model defaults document.

P95 is chosen over average (too optimistic, causes TPM overruns) and max_output_tokens (too pessimistic, wastes TPM budget).

### Effective limits

```
effective_rpm = min(provider_tier1_rpm, client_supplied_rpm)
effective_tpm = min(provider_tier1_tpm, client_supplied_tpm)
```

Client-supplied caps are optional. If omitted, provider defaults are used.

---

## Prompt Lifecycle

```
PENDING → RUNNING       scheduler dispatches prompt
RUNNING → COMPLETED     worker receives successful LLM response
RUNNING → PENDING       worker receives 429 or transient error (retry_count unchanged for 429)
RUNNING → FAILED        terminal error, or retry_count >= max_retries
RUNNING → PENDING       stale recovery sweep (worker crashed, no state written)
```

A job reaches `COMPLETED` when `completed + failed == total`. A job can complete with some failed prompts — all completable work was done.

---

## Job Cancellation

Client sends `DELETE /jobs/{job_id}`.

1. API layer sets job status to `CANCELLED` in Firestore
2. Scheduler detects `CANCELLED` at the top of its next iteration and stops dispatching for that job
3. Tasks already delivered to workers finish naturally — results are written but the job remains `CANCELLED`
4. Pending tasks in the Cloud Tasks queue are purged

---

## Security

- Raw API keys are never stored. The `client_id` is a SHA-256 hash of the key.
- LLM provider keys are stored in Google Cloud Secret Manager, retrieved by workers at invocation time. Never included in task payloads, Firestore documents, or logs.
- GCS result access is via time-limited signed URLs. Clients never receive direct GCS credentials.
- Firestore security rules scope all reads and writes by `client_id`.

---

## Observability

All services are instrumented with OpenTelemetry.

```
Cloud Run API  ─┐
GKE Scheduler  ─┼──► OTEL Collector ──► Tempo  (traces)
Cloud Run Workers ┘                 ──► Loki   (logs)
                                    ──► Mimir  (metrics)
                                         │
                                         ▼
                                      Grafana
```

**Every prompt generates a distributed trace** spanning scheduler decision → Cloud Tasks enqueue → worker execution → LLM call → result write.

**Key metrics:** `rpm_utilization`, `tpm_utilization`, `queue_depth`, `success_rate`, `failure_rate`, `429_count`, `cooldown_events`, `token_usage`, `estimated_cost`, `prompt_latency_p99`.

**Structured logs** on every service carry: `job_id`, `client_id`, `prompt_id`, `provider`, `model`, `retry_count`, `latency_ms`, `error_code`.

---

## Technology Stack

| Component | Technology |
|---|---|
| API Layer | Cloud Run |
| Scheduler | GKE (single pod) |
| Task Queue | Cloud Tasks (per-client queues) |
| Workers | Cloud Run (autoscaling) |
| Operational State | Firestore |
| Object Storage | Google Cloud Storage |
| Secrets | Google Cloud Secret Manager |
| LLM Providers | OpenAI, Gemini, Anthropic |
| Tracing | OpenTelemetry → Tempo |
| Logs | OpenTelemetry → Loki |
| Metrics | OpenTelemetry → Mimir |
| Dashboards | Grafana |