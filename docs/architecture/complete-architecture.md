# PromptForge — Complete System Architecture

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Component Inventory](#2-component-inventory)
3. [Data Ownership](#3-data-ownership)
4. [Phase 1 — Job Initialization](#4-phase-1--job-initialization)
5. [Phase 2 — File Upload](#5-phase-2--file-upload)
6. [Phase 3 — Upload Finalization & Pod Spawning](#6-phase-3--upload-finalization--pod-spawning)
7. [Phase 4 — Execution Pod Startup](#7-phase-4--execution-pod-startup)
8. [Phase 5 — Execution Engine](#8-phase-5--execution-engine)
9. [Phase 6 — Rate Learning](#9-phase-6--rate-learning)
10. [Phase 7 — Checkpointing & Recovery](#10-phase-7--checkpointing--recovery)
11. [Phase 8 — Job Completion](#11-phase-8--job-completion)
12. [Storage Layout](#12-storage-layout)
13. [Failure Modes & Handling](#13-failure-modes--handling)
14. [Design Principles](#14-design-principles)
15. [Observability](#15-observability)

---

## 1. System Overview

PromptForge is a distributed LLM batch job processing platform.

A user submits a file of prompts. The system processes every prompt against a target LLM provider (OpenAI, Anthropic, etc.), respects provider rate limits, and stores results.

The system is fully event-driven. There is no polling anywhere in the pipeline.

High-level flow:

```
User
  ↓  POST /v1/jobs/init
Cloud Run API
  ↓  returns signed GCS URL + job_id
User
  ↓  PUT prompts.jsonl
GCS Bucket
  ↓  OBJECT_FINALIZE event
Eventarc
  ↓  HTTP POST (CloudEvent)
Cloud Run Job Launcher
  ↓  creates Execution Pod via K8s API
GKE Execution Pod
  ↓  streams prompts → calls LLM → writes results
GCS + Firestore
```

---

## 2. Component Inventory

| Component | Type | Role |
|---|---|---|
| Cloud Run API | Always-on Cloud Run service | Accepts job init requests from users |
| Firestore | Managed NoSQL DB | Job metadata, lifecycle state, api_key storage |
| GCS Bucket | Object storage | Prompts input, results output, checkpoints |
| Eventarc | GCP event routing | Routes GCS events to Cloud Run |
| Cloud Run Job Launcher | Ephemeral Cloud Run instance | Validates input, queues or spawns Execution K8s Job, then exits |
| GKE Execution Pod | GKE Job/Pod (one per job) | Full execution engine for a single job; auto-restarted by K8s on crash |

---

## 3. Data Ownership

```
Firestore
  ├── Job metadata (provider, model, rate limits, api_key, api_key_hash)
  ├── Job lifecycle status (AWAITING_UPLOAD → QUEUED → PENDING → PROCESSING → COMPLETED / FAILED)
  └── Timestamps (created_at, uploaded_at, queued_at, started_processing_at, completed_at)

GCS — Input Bucket (gs://promptforge-input)
  └── {api_key_hash}/{job_id}/prompts.jsonl   (uploaded by user via signed URL)
      Eventarc watches this bucket only.
      Deleted by Execution Pod on job completion.

GCS — Output Bucket (gs://promptforge-output)
  ├── {api_key_hash}/{job_id}/results_part_NNN.jsonl
  ├── {api_key_hash}/{job_id}/results_final.jsonl
  ├── {api_key_hash}/{job_id}/errors.jsonl
  └── {api_key_hash}/{job_id}/state.json
      No Eventarc on this bucket.

GKE Execution Pod (in-memory only)
  ├── offset, running, completed, failed counters
  ├── retry_queue
  ├── result_buffer
  ├── token_history
  └── rpm_target, tpm_target, p95_tokens
```

No prompt text is ever stored in Firestore.
No secrets are ever stored outside Secret Manager.
No database polling occurs anywhere.

---

## 4. Phase 1 — Job Initialization

**Trigger:** User calls `POST /v1/jobs/init`

**Request body:**

```json
{
  "provider": "openai",
  "model": "gpt-4o",
  "rpm": 500,
  "tpm": 100000,
  "max_retries": 3,
  "api_key": "sk-..."
}
```

**Cloud Run API actions:**

1. Hash api_key → `api_key_hash = SHA256(api_key)`
2. Generate unique `job_id`
3. Create Firestore record at `/jobs/{job_id}`
4. Generate signed GCS upload URL for `gs://promptforge-input/{api_key_hash}/{job_id}/prompts.jsonl`
5. Return `job_id` + signed URL to user

**Firestore record created:**

```json
{
  "job_id": "job_123",
  "status": "AWAITING_UPLOAD",

  "provider": "openai",
  "model": "gpt-4o",
  "api_key": "sk-...",
  "api_key_hash": "sha256:abc123...",

  "rpm": 500,
  "tpm": 100000,
  "max_retries": 3,

  "upload_path": "{api_key_hash}/job_123/prompts.jsonl",

  "prompt_count": null,
  "invalid_count": null,
  "file_size_bytes": null,

  "created_at": "<timestamp>",
  "uploaded_at": null,
  "queued_at": null,
  "started_processing_at": null,
  "completed_at": null
}
```

---

## 5. Phase 2 — File Upload

**Trigger:** User has received the signed URL

User uploads directly to GCS using HTTP PUT:

```
PUT <signed_url>
Content-Type: application/x-ndjson

{"prompt_id": 1, "prompt": "..."}
{"prompt_id": 2, "prompt": "..."}
{"prompt_id": 3, "prompt": "..."}
...
```

The file lands at:

```
gs://promptforge-input/jobs/job_123/prompts.jsonl
```

Every prompt must have a unique `prompt_id`. This is assigned by the user or client SDK. It is critical for recovery and deduplication.

The Cloud Run API is not involved in the upload. The user writes directly to GCS via the signed URL.

---

## 6. Phase 3 — Upload Finalization & Pod Spawning

**Trigger:** GCS fires `OBJECT_FINALIZE` event when the upload completes

### Event flow:

```
GCS OBJECT_FINALIZE
        ↓
Eventarc (event type: google.cloud.storage.object.v1.finalized)
        ↓  HTTP POST (CloudEvent payload)
Cloud Run Job Launcher
        ↓
  1. Parse event → extract job_id, file_path, file_size_bytes
  2. Read job metadata from Firestore
  3. Stream-validate prompts.jsonl line by line (Pydantic)
  4. Check if another job for same api_key_hash is QUEUED or PROCESSING
       → if yes:  mark this job PENDING, exit (no pod created)
       → if no:   update Firestore + create K8s Job
  5. Cloud Run instance exits
```

### Step 3 — Stream Validation

The Job Launcher streams `prompts.jsonl` line by line from GCS and validates each line against `PromptSchema`:

```python
class PromptSchema(BaseModel):
    prompt_id: int
    prompt: str
```

Lines are processed one at a time — the full file is never loaded into memory.

- **Valid line** → `prompt_count += 1`, continue
- **Invalid line** → written immediately to `gs://promptforge-output/jobs/{job_id}/errors.jsonl`:

```json
{"prompt_id": null, "line": 42, "raw": "...", "error": "field 'prompt' missing", "stage": "validation"}
```

The job is **never aborted** due to invalid lines. Processing continues to the end of the file. Only fully unreadable files (e.g. not JSONL at all) result in a `FAILED` status.

After the full pass:

```
prompt_count  = number of valid lines
invalid_count = number of invalid lines
```

### Step 4 — Firestore Update + Pod Creation

```
Update Firestore:
  status          = QUEUED
  uploaded_at     = now()
  queued_at       = now()
  file_size_bytes = <from event>
  prompt_count    = <valid lines counted during validation>
  invalid_count   = <invalid lines written to errors.jsonl>

→ Create K8s Job (only valid prompts will be dispatched by the Execution Pod)
```

### Execution Pod spec (injected as environment variables):

```
JOB_ID           = job_123
PROVIDER         = openai
MODEL            = gpt-4o
RPM_LIMIT        = 500
TPM_LIMIT        = 100000
MAX_RETRIES      = 3
API_KEY_REF      = projects/my-project/secrets/openai-key/versions/latest
INPUT_BUCKET     = promptforge-input
OUTPUT_BUCKET    = promptforge-output
PROMPTS_PATH     = jobs/job_123/prompts.jsonl
PROMPT_COUNT     = 94500
```

The Cloud Run Job Launcher streams the input file once for validation, updates Firestore, creates the K8s Job, and exits. It holds no state and runs no loops.

---

## 7. Phase 4 — Execution Pod Startup

The Execution Pod starts in GKE. It is a single pod dedicated entirely to `job_123`. No other job shares this pod.

The Execution Pod runs as a Kubernetes `Job` (`restartPolicy: OnFailure`). If the container crashes, Kubernetes automatically schedules a new pod. The new pod reads `state.json` from GCS and resumes from the last checkpoint. A bare pod would not be restarted and the job would be lost.

**On startup:**

1. Read all env vars (injected by Job Launcher)
2. Call Secret Manager using `API_KEY_REF` → fetch actual provider API key into memory
3. Initialize in-memory execution state:

```python
offset          = 0          # current read position in prompts.jsonl
completed       = 0          # successfully processed prompts
failed          = 0          # permanently failed prompts (exhausted retries)
running         = 0          # in-flight requests

retry_queue     = []         # prompts waiting to be retried
result_buffer   = []         # responses buffered before GCS write
token_history   = []         # rolling window of actual token counts per response

rpm_target      = 10         # starting RPM (conservative)
tpm_target      = TPM_LIMIT  # from env var
p95_tokens      = 1000       # initial estimate, updated from real responses

dispatch_enabled = True
learning_mode   = "slow_start"
```

4. Check GCS for existing `state.json` — if found, restore checkpoint (recovery path)
5. Update Firestore: `status = PROCESSING`, `started_processing_at = now()`
6. Start all four internal loops concurrently

---

## 8. Phase 5 — Execution Engine

The Execution Pod runs four concurrent loops:

```
┌──────────────────────┐
│   Dispatch Loop      │  — decides when and what to send
└──────────┬───────────┘
           ↓
┌──────────────────────┐
│   Response Handler   │  — processes LLM responses asynchronously
└──────────┬───────────┘
           ↓
┌──────────────────────┐
│   Result Buffer      │  — accumulates responses before writing
└──────────┬───────────┘
           ↓
┌──────────────────────┐
│   Checkpoint Writer  │  — persists state periodically
└──────────────────────┘
```

All loops run simultaneously. They do not block each other.

---

### Dispatch Loop

The dispatch loop continuously evaluates: can I send another request right now?

**Effective RPM calculation:**

```python
effective_rpm = min(
    rpm_target,
    tpm_target / p95_tokens
)
```

Example:

```
rpm_target  = 500
tpm_limit   = 100000
p95_tokens  = 1000

effective_rpm = min(500, 100000 / 1000)
             = min(500, 100)
             = 100 req/min
```

TPM is the bottleneck here. The dispatch loop automatically respects whichever limit is tighter.

**Request spacing:**

The scheduler does not batch and burst. It spaces requests evenly:

```
interval = 60 / effective_rpm

effective_rpm = 100
interval      = 0.6 seconds
```

Requests leave at:

```
t=0.0   send request
t=0.6   send request
t=1.2   send request
t=1.8   send request
...
```

This creates a smooth, continuous flow rather than waves.

**Prompt selection priority:**

```python
if retry_queue:
    prompt = retry_queue.pop()
else:
    prompt = next_prompt(offset)
    offset += 1
```

Retry queue always takes priority. New prompts are only read when the retry queue is empty.

**GCS streaming read:**

The pod never downloads `prompts.jsonl` in full. It opens a byte-range stream and reads one line at a time:

```
open GCS stream at byte offset
        ↓
read next newline-delimited JSON line
        ↓
parse prompt
        ↓
advance offset
```

Memory usage is constant regardless of file size. A file with 10 million prompts uses the same memory as one with 100.

**Dispatching:**

Each prompt becomes an async task:

```python
asyncio.create_task(call_llm(prompt, api_key, provider, model))
running += 1
```

The LLM call is a direct HTTPS request to the provider endpoint. No intermediate service.

---

### Response Handler

Responses return asynchronously and out of order. Order does not matter — every response carries its `prompt_id`.

**Success (HTTP 200):**

```python
running -= 1
completed += 1

actual_tokens = response.usage.prompt_tokens + response.usage.completion_tokens
token_history.append(actual_tokens)
p95_tokens = percentile(token_history, 95)

result_buffer.append({
    "prompt_id": response.prompt_id,
    "response": response.text,
    "prompt_tokens": response.usage.prompt_tokens,
    "completion_tokens": response.usage.completion_tokens,
    "total_tokens": actual_tokens
})
```

**Retryable failure (429, 500, 502, 503, 504, timeout):**

```python
running -= 1
prompt.attempt += 1

if prompt.attempt < MAX_RETRIES:
    retry_queue.append(prompt)
else:
    # permanently failed
    failed += 1
    error_buffer.append({
        "prompt_id": prompt.prompt_id,
        "prompt": prompt.text,
        "attempts": prompt.attempt,
        "last_error_code": response.status_code,
        "last_error_message": response.error_message,
        "failed_at": now()
    })
```

**Non-retryable failure (400, 401, 403):**

Treated as permanent failure immediately without retry. Written to `error_buffer` with `attempts = 1`.

---

### Result Buffer

Responses are not written to GCS one by one. They accumulate in `result_buffer`.

**Flush when any condition is met:**

```
500 responses accumulated
OR
50 MB of buffered data
OR
30 seconds since last flush
```

**On flush:**

```
result_buffer contents
        ↓
serialize to JSONL
        ↓
upload to GCS: results_part_001.jsonl
        ↓
result_buffer.clear()
```

Part files are numbered sequentially:

```
results_part_001.jsonl
results_part_002.jsonl
results_part_003.jsonl
...
```

Without buffering, 1 million prompts = 1 million GCS writes.
With buffering (500 per flush), 1 million prompts = ~2000 GCS writes.

Error buffer follows the same flush strategy, writing to `errors.jsonl` (appended, not partitioned).

---

### Checkpoint Writer

Every 30 seconds, or immediately after a result buffer flush:

```json
{
  "offset": 125000,
  "completed": 120000,
  "failed": 300,
  "rpm_target": 375,
  "tpm_target": 100000,
  "p95_tokens": 850
}
```

Uploaded to:

```
gs://bucket/jobs/job_123/state.json
```

This file is overwritten on every checkpoint (not appended). It always reflects the latest known good state.

---

## 9. Phase 6 — Rate Learning

The scheduler learns the real provider limits dynamically. It does not rely on the user-provided `rpm` and `tpm` values as hard limits — it uses them as upper bounds while discovering the actual live limits.

### Slow Start

Before any 429 is received:

Every 30 seconds:

```python
rpm_target *= 1.5
```

Progression:

```
10 → 15 → 22 → 33 → 49 → 73 → 110 → 165 → ...
```

This rapidly finds the provider's ceiling without requiring prior knowledge.

### First 429 — Backoff

When a 429 is received:

```python
dispatch_enabled = False   # stop sending new requests immediately
```

In-flight requests already sent cannot be stopped. Wait for all to drain:

```python
while running > 0:
    await asyncio.sleep(0.1)
```

Then reduce target:

```python
rpm_target = rpm_target * 0.75
```

Apply cooldown:

```python
await asyncio.sleep(60)
```

Switch mode:

```python
learning_mode = "congestion_avoidance"
dispatch_enabled = True
```

### Congestion Avoidance

After the first 429, the scheduler knows roughly where the ceiling is.

Instead of multiplying, it probes slowly:

```python
# every 30 seconds with no 429:
rpm_target += 1
```

Progression after backoff to 375:

```
375 → 376 → 377 → 378 → ...
```

This finds unused headroom without triggering another rate limit.

If a second 429 occurs, the same backoff cycle repeats from the current `rpm_target`.

### TPM Learning

Every successful response updates `p95_tokens`:

```python
token_history.append(actual_tokens)
p95_tokens = percentile(token_history[-1000:], 95)   # rolling window of 1000
```

This value feeds directly into the effective RPM calculation:

```python
effective_rpm = min(rpm_target, tpm_target / p95_tokens)
```

If prompts start using more tokens (longer responses), `p95_tokens` rises, `effective_rpm` drops automatically. No manual tuning required.

---

## 10. Phase 7 — Checkpointing & Recovery

### Normal Operation

State is checkpointed every 30 seconds and after every result flush.

`state.json` always contains:

```json
{
  "offset": <lines read so far>,
  "completed": <successful prompts>,
  "failed": <permanently failed prompts>,
  "rpm_target": <current learned rpm>,
  "tpm_target": <tpm limit>,
  "p95_tokens": <current p95 estimate>
}
```

### SIGTERM (Kubernetes eviction or scale-down)

Kubernetes sends `SIGTERM` before killing a pod.

```python
signal.signal(signal.SIGTERM, handle_sigterm)

def handle_sigterm():
    dispatch_enabled = False
    # in-flight requests drain naturally
    # checkpoint writer fires one final time
    # pod exits cleanly
```

No data loss. The next pod will resume from the last checkpoint.

### Pod Crash Recovery

If the pod dies without a clean SIGTERM:

1. Kubernetes restarts the pod (or a new pod is scheduled)
2. Pod starts up normally
3. On startup, checks GCS for `state.json`
4. Finds it — restores:

```python
offset      = state["offset"]
rpm_target  = state["rpm_target"]
tpm_target  = state["tpm_target"]
p95_tokens  = state["p95_tokens"]
```

5. Resumes processing from `offset`

**At-least-once semantics:** Prompts processed after the last checkpoint but before the crash will be reprocessed. This means some prompts may be sent to the LLM twice. This is an accepted tradeoff.

The design prefers duplicate work over lost work.

---

## 11. Phase 8 — Job Completion

The job is complete when:

```python
offset == EOF          # all prompts have been read
AND running == 0       # no in-flight requests
AND retry_queue == []  # nothing waiting to be retried
```

**Final actions (in order):**

```
1. Flush result_buffer → results_final.jsonl → GCS
2. Flush error_buffer  → errors.jsonl        → GCS
3. Write final state.json                    → GCS
4. Run reconciliation pass
5. Delete prompts.jsonl from input bucket
6. Update Firestore: status=COMPLETED, completed_at=now()
7. Check Firestore for oldest PENDING job with same api_key_hash
     → if found: update it to QUEUED, create K8s Job for it
8. Pod exits
```

### Reconciliation Pass

After all buffers are flushed, the pod runs a reconciliation pass to detect any prompts that were silently lost (e.g. dropped during a crash between checkpoints).

Since `prompt_id` values are sequential integers (`1..PROMPT_COUNT`), the full expected set is known without re-reading the input file.

**Implementation uses a bitset — 10M prompts = 1.25MB regardless of job size:**

```python
seen = bitarray(PROMPT_COUNT + 1)
seen.setall(0)

# stream results_part_*.jsonl + results_final.jsonl
for record in stream_results(GCS_OUT):
    seen[record.prompt_id] = 1

# stream errors.jsonl
for record in stream_errors(GCS_OUT):
    seen[record.prompt_id] = 1

# find missing
missing = [i for i in range(1, PROMPT_COUNT + 1) if not seen[i]]
```

**If missing prompt IDs are found:**

```python
for prompt_id in missing:
    otel.log(
        level  = "WARNING",
        event  = "prompt_lost",
        job_id = job_id,
        prompt_id = prompt_id
    )
```

No retry. No reprocessing. Logged to OTel only — visible in traces and structured logs. The job still completes as `COMPLETED`. The user can query OTel to find exactly which IDs were lost.

### Input Cleanup

After reconciliation, the pod deletes the source file — it is no longer needed:

```
delete gs://promptforge-input/{api_key_hash}/{job_id}/prompts.jsonl
```

The input prefix is now empty. Output files remain in the output bucket until the GCS lifecycle policy expires them.

Firestore `status = COMPLETED` is the signal to any downstream consumer that results are available in GCS.

---

## 12. Storage Layout

Final GCS layout for a completed job:

```
gs://promptforge-input/jobs/job_123/
└── prompts.jsonl            — original input (uploaded by user)

gs://promptforge-output/jobs/job_123/
├── state.json               — final checkpoint state
├── results_part_001.jsonl   — batched results (flushed during execution)
├── results_part_002.jsonl
├── results_part_003.jsonl
├── ...
├── results_final.jsonl      — last batch (flushed at completion)
└── errors.jsonl             — permanently failed prompts
```

**results_part_NNN.jsonl structure:**

```json
{"prompt_id": 1, "response": "...", "prompt_tokens": 200, "completion_tokens": 600, "total_tokens": 800}
{"prompt_id": 2, "response": "...", "prompt_tokens": 150, "completion_tokens": 450, "total_tokens": 600}
```

**errors.jsonl structure:**

```json
{"prompt_id": 47, "prompt": "...", "attempts": 3, "last_error_code": 429, "last_error_message": "Rate limit exceeded", "failed_at": "<timestamp>"}
{"prompt_id": 91, "prompt": "...", "attempts": 3, "last_error_code": 503, "last_error_message": "Service unavailable", "failed_at": "<timestamp>"}
```

---

## 13. Failure Modes & Handling

| Failure | Detection | Response |
|---|---|---|
| Upload never happens | Client-side timeout / user error | Firestore record stays AWAITING_UPLOAD indefinitely. TTL cleanup job can expire stale records. |
| OBJECT_FINALIZE not received | Eventarc delivery failure | Eventarc retries with exponential backoff automatically. |
| Job Launcher Cloud Run fails | K8s pod never created | Eventarc retries trigger → Job Launcher runs again. Job Launcher must be idempotent (check if pod already exists before creating). |
| Execution Pod OOM / crash | Pod restarts | Resumes from last state.json checkpoint. |
| Execution Pod SIGTERM | Kubernetes sends signal | dispatch_enabled=False, in-flight drains, final checkpoint, clean exit. |
| LLM provider 429 | Response handler | dispatch_enabled=False, drain in-flight, reduce rpm_target * 0.75, cooldown 60s, re-enable. |
| Prompt exceeds max_retries | Response handler | Written to errors.jsonl, counted as failed. |
| GCS write fails | Result buffer flush error | Retry flush. Do not clear buffer until write succeeds. |
| Firestore write fails | Status update error | Retry with backoff. Non-critical for execution — pod continues regardless. |

---

## 14. Design Principles

**Event-driven end to end.**
No component polls. Every transition is triggered by an event: HTTP response, GCS event, Firestore listener, async task completion.

**One pod per job.**
Each job is fully isolated. A failure in one job cannot affect another. No shared execution state.

**Memory-constant streaming.**
Prompts are never loaded in bulk. One line at a time from GCS. A 10-million-prompt job uses the same pod memory as a 100-prompt job.

**No intermediate queue between scheduler and LLM.**
The Execution Pod calls the LLM provider directly. No Pub/Sub, no worker fleet. The async dispatch loop is the queue.

**Dynamic rate learning.**
The scheduler does not trust user-provided rate limits as exact. It discovers real limits through slow-start and congestion avoidance, exactly like TCP congestion control.

**Batch writes, not per-response writes.**
Results are buffered and flushed in batches. 1 million prompts produce ~2000 GCS writes instead of 1 million.

**At-least-once over at-most-once.**
On recovery, prompts near the checkpoint boundary may be reprocessed. This is preferred over silently dropping prompts.

**API key management via Unkey.**
Client API keys are issued and validated through Unkey — not stored or hashed manually. Unkey handles generation, hashing, revocation, and per-key rate limiting. The `client_id` comes from Unkey key metadata and is used as the GCS path namespace and Firestore job owner. The provider LLM api_key (submitted per job) is stored in the Firestore job record only — never logged or exported.

**Sequential job execution per client.**
Jobs from the same api_key_hash run one at a time. The Job Launcher checks for active jobs before creating a pod. The Execution Pod triggers the next queued job on completion.

**Pod sized to workload, not over-provisioned.**
Memory request is set to cover Python runtime (~70MB) + result buffer (~1.5MB) + in-flight tasks (~1MB) with headroom. 128Mi request, 256Mi limit is sufficient for all supported RPM levels.

---

## 15. Observability

PromptForge uses **OpenTelemetry (OTel)** as the instrumentation layer across all components. The backend is pluggable — OTel exports to Google Cloud (Trace, Logging, Monitoring) or a self-hosted Grafana Stack (Tempo, Loki, Mimir) without changing application code.

### What Emits Telemetry

| Component | Emits |
|---|---|
| Cloud Run API Service | Traces (job.init span), request logs |
| Cloud Run Job Launcher | Traces (job.launch span), pod creation logs |
| GKE Execution Pod | All metrics, all prompt-level traces, all structured logs |

---

### Traces

Every job is a top-level trace. Every prompt dispatch is a child span.

```
Trace: job_123
  │
  ├── span: job.init
  │     attrs: job_id, provider, model, rpm_limit, tpm_limit
  │
  ├── span: job.launch
  │     attrs: job_id, file_size_bytes, prompt_count
  │     events: firestore_updated, k8s_job_created
  │
  ├── span: job.execute  (entire pod lifetime)
  │     │
  │     ├── span: prompt.dispatch  [prompt_id=1, attempt=1]
  │     │     attrs:  job_id, prompt_id, attempt, provider, model
  │     │     events: request_sent, response_received
  │     │     attrs:  prompt_tokens, completion_tokens, total_tokens, latency_ms
  │     │     status: OK
  │     │
  │     ├── span: prompt.dispatch  [prompt_id=47, attempt=2]
  │     │     attrs:  job_id, prompt_id, attempt=2, error_code=429
  │     │     status: ERROR
  │     │
  │     ├── span: buffer.flush  [batch=001]
  │     │     attrs:  job_id, batch_number, response_count, flush_trigger, latency_ms
  │     │
  │     └── span: checkpoint.write
  │           attrs:  job_id, offset, completed, failed, rpm_target, p95_tokens
  │
  └── span: job.complete
        attrs: job_id, total_completed, total_failed, total_tokens, duration_ms
```

---

### Metrics

All metrics carry a `job_id` label at minimum.

**Prompt counters:**

```
promptforge.prompts.dispatched     [job_id, provider, model]
promptforge.prompts.completed      [job_id, provider, model]
promptforge.prompts.failed         [job_id, provider, model, error_code]
promptforge.prompts.retried        [job_id, attempt_number, error_code]
```

**Token histograms:**

```
promptforge.tokens.prompt          histogram [job_id, model]
promptforge.tokens.completion      histogram [job_id, model]
promptforge.tokens.total           histogram [job_id, model]
```

**LLM call performance:**

```
promptforge.llm.latency_ms         histogram [provider, model, status]
```

**Rate learning state (gauges — sampled over time):**

```
promptforge.rate.rpm_target        gauge [job_id]
promptforge.rate.effective_rpm     gauge [job_id]
promptforge.rate.p95_tokens        gauge [job_id]
promptforge.rate.429_events        counter [job_id, provider]
promptforge.rate.cooldown_active   gauge [job_id]
```

**Queue and buffer health:**

```
promptforge.queue.running          gauge [job_id]
promptforge.queue.retry_depth      gauge [job_id]
promptforge.buffer.size            gauge [job_id]
```

**Job level:**

```
promptforge.job.duration_ms        histogram [provider, model]
promptforge.pod.restarts           counter [job_id]
```

> Note on cardinality: `prompt_id` must never be used as a metric label — it has unbounded cardinality. It belongs only on trace spans and structured logs.

---

### Structured Logs

Every log line is structured JSON with a consistent base schema:

```json
{
  "timestamp": "2026-06-03T10:23:45Z",
  "level": "ERROR",
  "event": "prompt_failed",

  "job_id": "job_123",
  "prompt_id": 47,
  "attempt": 2,

  "provider": "openai",
  "model": "gpt-4o",

  "error_code": 429,
  "error_message": "Rate limit exceeded",

  "prompt_tokens": 200,
  "completion_tokens": 0,
  "total_tokens": 200,

  "latency_ms": 312,
  "rpm_target": 375,
  "p95_tokens": 850
}
```

Log events emitted by the Execution Pod:

| Event | Level | When |
|---|---|---|
| `job_started` | INFO | Pod startup complete |
| `checkpoint_restored` | INFO | state.json found on startup |
| `prompt_dispatched` | DEBUG | Each LLM request sent |
| `prompt_completed` | DEBUG | Successful LLM response |
| `prompt_retried` | WARN | Prompt pushed back to retry_queue |
| `prompt_failed` | ERROR | Prompt exhausted max_retries |
| `rate_429_received` | WARN | 429 from provider |
| `rate_backoff_applied` | WARN | rpm_target reduced |
| `buffer_flushed` | INFO | Result batch written to GCS |
| `checkpoint_written` | INFO | state.json written |
| `job_completed` | INFO | All prompts processed |
| `sigterm_received` | WARN | Pod shutting down cleanly |
| `pod_recovering` | WARN | Resuming from checkpoint after restart |

---

### Backend

The OTel SDK is configured once. The export destination is an environment variable.

| Tool | Role | Notes |
|---|---|---|
| **Axiom** | Logs + Traces + Metrics | OTel-native SaaS. One env var to connect. Free tier covers early stage. No infrastructure to run. |
| **Sentry** | Error tracking | Captures unhandled exceptions across all services with full stack traces. 3 lines to integrate. |
| **Better Stack** | Uptime + alerting | External endpoint monitoring. Pages on downtime. Public status page for users. |

Switching Axiom to any other OTel-compatible backend requires only changing `OTEL_EXPORTER_OTLP_ENDPOINT`. No application code changes.
