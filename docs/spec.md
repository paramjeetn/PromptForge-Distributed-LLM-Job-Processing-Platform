# PromptForge — System Specification

Version: 2.0
Status: Current
Platform: Google Cloud

---

## 1. API Specification

### Base URL

```
https://api.promptforge.io/v1
```

### Authentication

All requests require a client API key in the header:

```
X-API-Key: {api_key}
```

Keys are issued and validated through **Unkey**. The raw key is never stored anywhere in PromptForge infrastructure. On every request, the API layer calls `unkey.keys.verify(key)` — a valid response returns the associated `client_id` from Unkey metadata. An invalid or revoked key returns `401` immediately.

---

### POST /v1/jobs/init

Initialize a new job. Returns a signed GCS URL for the client to upload their prompts file directly.

**Request body:**

```json
{
  "provider": "openai | anthropic | gemini | mistral",
  "model": "string",
  "max_retries": 3,
  "rpm": null,
  "tpm": null
}
```

| Field | Required | Description |
|---|---|---|
| `provider` | Yes | LLM provider. Routed via LiteLLM — adding new providers requires no code change. |
| `model` | Yes | Provider model identifier (e.g. `gpt-4o`, `claude-opus-4-6`, `gemini-2.5-pro`) |
| `max_retries` | No | Per-prompt retry limit for transient errors. Default: 3 |
| `rpm` | No | Client RPM cap. Used as upper bound by the rate learning engine. |
| `tpm` | No | Client TPM cap. Used as upper bound by the rate learning engine. |

**Response 202 Accepted:**

```json
{
  "job_id": "uuid",
  "upload_url": "https://storage.googleapis.com/...",
  "expires_at": "ISO8601"
}
```

The client uploads `prompts.jsonl` directly to `upload_url` via HTTP PUT. PromptForge never proxies the file.

**Error responses:**

| Code | Reason |
|---|---|
| `400` | Invalid payload or missing required fields |
| `401` | Invalid or revoked API key |
| `422` | Unsupported provider or model |

---

### GET /v1/jobs/{job_id}

Get current job status and progress counters.

**Response 200 OK:**

```json
{
  "job_id": "uuid",
  "status": "AWAITING_UPLOAD | QUEUED | PENDING | PROCESSING | COMPLETED | FAILED | CANCELLED",
  "total": 1000,
  "completed": 530,
  "failed": 20,
  "progress_pct": 55.0,
  "created_at": "ISO8601",
  "started_processing_at": "ISO8601",
  "completed_at": "ISO8601"
}
```

**Status values:**

| Status | Meaning |
|---|---|
| `AWAITING_UPLOAD` | Job record created. Waiting for client to upload prompts.jsonl. |
| `QUEUED` | File uploaded and validated. Pod creation in progress or pod starting. |
| `PENDING` | Another job for same client is already running. This job is queued behind it. |
| `PROCESSING` | Execution pod is running. Prompts being dispatched. |
| `COMPLETED` | All prompts processed. Results available for download. |
| `FAILED` | File was invalid (not parseable JSONL) or a fatal system error occurred. |
| `CANCELLED` | Client cancelled the job. |

**Error responses:**

| Code | Reason |
|---|---|
| `401` | Invalid API key |
| `404` | Job not found or does not belong to this client |

---

### GET /v1/jobs/{job_id}/results

Get signed download URLs for completed result files.

Only available when `status = COMPLETED`. Returns `409` if the job is still running or has not started.

**Response 200 OK:**

```json
{
  "job_id": "uuid",
  "result_urls": [
    "https://signed-gcs-url/results_part_001.jsonl",
    "https://signed-gcs-url/results_part_002.jsonl",
    "https://signed-gcs-url/results_final.jsonl"
  ],
  "error_urls": [
    "https://signed-gcs-url/errors.jsonl"
  ],
  "expires_at": "ISO8601"
}
```

Signed URLs expire after 1 hour. Each URL points to one JSONL part file. Clients merge all `result_urls` files to reconstruct the complete result set. `error_urls` contains prompts that exhausted all retries.

**Error responses:**

| Code | Reason |
|---|---|
| `401` | Invalid API key |
| `404` | Job not found or does not belong to this client |
| `409` | Job not yet completed |

---

### DELETE /v1/jobs/{job_id}

Cancel a running or queued job.

The execution pod detects cancellation on the next dispatch loop iteration and stops sending new prompts. Prompts already in-flight complete naturally. The pod then exits.

**Response 200 OK:**

```json
{
  "job_id": "uuid",
  "status": "CANCELLED"
}
```

**Error responses:**

| Code | Reason |
|---|---|
| `401` | Invalid API key |
| `404` | Job not found or does not belong to this client |
| `409` | Job already completed or already cancelled |

---

## 2. Prompt File Format

Clients upload a JSONL file where every line is a valid JSON object with at minimum:

```json
{"prompt_id": 1, "prompt": "What is the capital of France?"}
{"prompt_id": 2, "prompt": "Summarise the following text: ..."}
{"prompt_id": 3, "prompt": "..."}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `prompt_id` | integer | Yes | Unique integer ID. Must be sequential from 1 to N. Used for recovery, deduplication, and reconciliation. |
| `prompt` | string | Yes | Prompt text sent to the LLM. |

Additional fields are ignored. Lines that fail validation are written to `errors.jsonl` at upload time with the reason. The job is not aborted due to invalid lines — only completely non-JSONL files result in `FAILED`.

---

## 3. Result File Format

**results_part_NNN.jsonl / results_final.jsonl:**

```json
{"prompt_id": 1, "response": "Paris", "prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}
{"prompt_id": 2, "response": "...", "prompt_tokens": 340, "completion_tokens": 210, "total_tokens": 550}
```

**errors.jsonl:**

```json
{"prompt_id": 47, "prompt": "...", "attempts": 3, "last_error_code": 429, "last_error_message": "Rate limit exceeded", "failed_at": "ISO8601"}
{"prompt_id": 91, "prompt": "...", "attempts": 3, "last_error_code": 503, "last_error_message": "Service unavailable", "failed_at": "ISO8601"}
```

Validation errors written at upload time (before execution):

```json
{"prompt_id": null, "line": 42, "raw": "...", "error": "field 'prompt' missing", "stage": "validation"}
```

---

## 4. Firestore Schema

### jobs/{job_id}

| Field | Type | Description |
|---|---|---|
| `job_id` | string | UUID, immutable |
| `client_id` | string | From Unkey key metadata. Used as GCS path namespace and job owner. |
| `provider` | string | LLM provider |
| `model` | string | Model identifier |
| `api_key` | string | Provider LLM API key submitted by client. Stored here only — never logged or exported. |
| `status` | string | AWAITING_UPLOAD / QUEUED / PENDING / PROCESSING / COMPLETED / FAILED / CANCELLED |
| `rpm` | int | Client-supplied RPM upper bound (nullable) |
| `tpm` | int | Client-supplied TPM upper bound (nullable) |
| `max_retries` | int | Per-prompt retry limit |
| `upload_path` | string | GCS path of prompts.jsonl |
| `prompt_count` | int | Valid prompt count (set after upload validation) |
| `invalid_count` | int | Invalid lines found during upload validation |
| `file_size_bytes` | int | File size (set after upload) |
| `created_at` | timestamp | |
| `uploaded_at` | timestamp | When GCS OBJECT_FINALIZE fired |
| `queued_at` | timestamp | When pod creation was triggered |
| `started_processing_at` | timestamp | When execution pod began dispatching |
| `completed_at` | timestamp | When job reached terminal state |

No prompt text is stored in Firestore. No per-prompt records. No quota collection. All rate learning state lives in execution pod memory.

---

## 5. GCS Layout

```
gs://promptforge-input/
  {client_id}/{job_id}/prompts.jsonl       ← uploaded by client, deleted on completion

gs://promptforge-output/
  {client_id}/{job_id}/state.json          ← execution checkpoint (overwritten every 30s)
  {client_id}/{job_id}/results_part_001.jsonl
  {client_id}/{job_id}/results_part_002.jsonl
  {client_id}/{job_id}/...
  {client_id}/{job_id}/results_final.jsonl
  {client_id}/{job_id}/errors.jsonl
```

Eventarc watches `promptforge-input` only. No Eventarc on the output bucket.

---

## 6. Job Status Machine

```
AWAITING_UPLOAD
      ↓  (OBJECT_FINALIZE received, file valid)
   QUEUED  ←──────────────────────────────────────────────┐
      ↓  (no other job running for same client)            │
  PROCESSING                                               │
      ↓  (offset==EOF, running==0, retry_queue==[])        │
  COMPLETED ──→ triggers next PENDING job for same client ─┘

AWAITING_UPLOAD
      ↓  (OBJECT_FINALIZE received, another job active)
   PENDING
      ↓  (previous job completes)
   QUEUED  → PROCESSING → COMPLETED

AWAITING_UPLOAD / QUEUED / PROCESSING
      ↓  (client calls DELETE)
  CANCELLED

QUEUED
      ↓  (file completely invalid — not parseable as JSONL)
   FAILED
```

---

## 7. Error Classification

| HTTP Code | Class | Retried | Behaviour |
|---|---|---|---|
| 2xx | Success | — | Response written to result buffer |
| 429 | Rate limit | Yes (after cooldown) | Dispatch paused, rpm_target reduced, prompt back to retry queue |
| 500, 502, 503, 504 | Transient | Yes | Prompt back to retry queue if attempts < max_retries |
| 408, timeout | Transient | Yes | Same as 5xx transient |
| 400, 401, 403, 422 | Terminal | No | Written to errors.jsonl immediately |

---

## 8. Rate Learning

The execution pod learns the provider's real-time RPM and TPM limits dynamically. It does not rely on user-supplied `rpm`/`tpm` as exact values — they are upper bounds only.

**Slow Start** (before any 429): `rpm_target *= 1.5` every 30 seconds. Progression: `10 → 15 → 22 → 33 → 49 → 73 → 110...`

**On 429**: `dispatch_enabled = False` → drain in-flight → `rpm_target *= 0.75` → 60s cooldown → resume. Switch to congestion avoidance.

**Congestion Avoidance** (after first 429): `rpm_target += 1` every 30 seconds. Slowly probes for unused headroom.

**TPM**: `p95_tokens` is updated after every successful response using a rolling window of 1000 samples. `effective_rpm = min(rpm_target, tpm_limit / p95_tokens)`. Requests are spaced evenly: `interval = 60 / effective_rpm`.

All rate state is in-memory. None of it touches Firestore.

---

## 9. Security

| Concern | Implementation |
|---|---|
| Client API keys | Managed by Unkey. Never stored in PromptForge DB. Revocable via Unkey dashboard. |
| Provider LLM keys | Stored in Firestore job record. Never logged, never exported to OTel. |
| File upload | Client writes directly to GCS via signed URL. API never handles file contents. |
| Result access | Signed GCS URLs with 1hr expiry. Only the owning client can request them. |
| Data isolation | GCS paths and Firestore records scoped by `client_id`. |
| OTel exports | Prompt text stripped before export. Only IDs, counts, codes, and timings leave GCP. |

---

## 10. Observability

| Tool | Role |
|---|---|
| **Axiom** | Logs, traces, and metrics via OTel. One env var to connect. |
| **Sentry** | Error tracking. Unhandled exceptions captured across all services with stack traces. |
| **Better Stack** | External uptime monitoring. Public status page. |

Every job is a root OTel trace. Every prompt dispatch is a child span carrying: `prompt_id`, `attempt`, `provider`, `model`, `latency_ms`, `prompt_tokens`, `completion_tokens`, `error_code`.

Key metrics emitted: `rate.rpm_target`, `rate.effective_rpm`, `rate.p95_tokens`, `rate.429_events`, `prompts.completed`, `prompts.failed`, `llm.latency_ms`, `queue.retry_depth`, `buffer.size`.

`prompt_id` is never used as a metric label (unbounded cardinality). Traces and structured logs only.

---

## 11. Technology Stack

| Component | Technology |
|---|---|
| API Layer | Cloud Run (Python) |
| Job Launcher | Cloud Run (Python, ephemeral) |
| Execution Engine | GKE Pod — one per job (Python, asyncio) |
| LLM Provider Abstraction | LiteLLM — provider unification only |
| Job State | Firestore |
| Object Storage | Google Cloud Storage |
| Event Routing | Eventarc (GCS OBJECT_FINALIZE) |
| API Key Management | Unkey |
| Infrastructure as Code | Pulumi (TypeScript) |
| Logs + Traces + Metrics | Axiom (via OTel) |
| Error Tracking | Sentry |
| Uptime Monitoring | Better Stack |
