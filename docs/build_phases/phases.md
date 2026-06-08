# PromptForge — Build Phases

Each phase is a self-contained, testable unit. It has defined inputs, defined outputs, and a clear integration boundary. Phases are designed to be built and verified independently before the next phase depends on them.

---

## Phase Map

```
Phase 1  →  Infrastructure & Data Layer
Phase 2  →  Job Init API
Phase 3  →  Upload Pipeline (Eventarc + Job Launcher)
Phase 4  →  Execution Engine — Baseline
Phase 5  →  Result Buffer & Storage
Phase 6  →  Rate Learning
Phase 7  →  Checkpointing & Recovery
Phase 8  →  Job Completion & Reconciliation
Phase 9  →  Status & Results API
Phase 10 →  Observability
```

Each phase depends only on the phases before it. No phase requires knowledge of phases ahead.

---

## Phase 1 — Infrastructure & Data Layer

### What it is
The foundation everything else runs on. No application logic. Pure infrastructure provisioning.

### Build
- GCS input bucket (`promptforge-input`)
- GCS output bucket (`promptforge-output`)
- Firestore database with collection: `jobs`
- GCP Secret Manager — store a test provider API key
- IAM service accounts with least-privilege roles for each service
- Eventarc trigger: watch `promptforge-input` for `OBJECT_FINALIZE`
- GKE cluster (single node pool to start)

### Input
Nothing. Greenfield.

### Output
```
GCS buckets accessible via SDK
Firestore read/write working
Secret Manager fetch working
Eventarc trigger configured
GKE cluster reachable
```

### Verified when
- You can write a Firestore document and read it back
- You can upload a file to GCS and confirm the Eventarc trigger fires (check Cloud Logging)
- You can fetch a secret from Secret Manager
- `kubectl get nodes` returns a healthy node

---

## Phase 2 — Job Init API

### What it is
The front door. A Cloud Run service that accepts a job submission, creates the Firestore record, and hands back a signed upload URL. No execution logic — just ingestion and state initialization.

### Build
- `POST /v1/jobs/init` endpoint (Cloud Run)
- **API key validation via Unkey** — on every request: `unkey.keys.verify(key=request.api_key)`
  - Valid key → extract `client_id` from Unkey metadata, proceed
  - Invalid / revoked key → `401` immediately, no Firestore read
  - Key provisioning (out of band): `unkey.keys.create(api_id="promptforge", meta={"client_id": uid})`
- Request validation: `provider`, `model`, `rpm`, `tpm`, `max_retries`
- Generate `job_id` (UUID)
- Derive `client_id` from Unkey key metadata (not from hashing the raw key)
- Create Firestore record at `/jobs/{job_id}` with `status = AWAITING_UPLOAD`
- Generate signed GCS upload URL for `gs://promptforge-input/{client_id}/{job_id}/prompts.jsonl`
- Return `job_id` + `upload_url` + `expires_at`
- `400` on malformed request body
- `422` on unsupported provider or model

### Input
```
Phase 1: Firestore, GCS, IAM service account with signBlob permission
Unkey account configured with a promptforge API
```

### Output
```
POST /v1/jobs/init  →  { job_id, upload_url, expires_at }
Firestore /jobs/{job_id}  status = AWAITING_UPLOAD
client_id derived from Unkey, stored in Firestore record
```

### Verified when
- `POST /v1/jobs/init` with valid Unkey key returns 202 with a signed URL
- Firestore document exists with all fields set
- `PUT <signed_url>` with a small test file succeeds (file lands in GCS)
- Missing `provider` field returns 400
- Revoked or invalid API key returns 401 (Unkey handles this)

---

## Phase 3 — Upload Pipeline (Eventarc + Job Launcher)

### What it is
The bridge between a file landing in GCS and a job entering execution. The Job Launcher is ephemeral — it wakes up on an event, does its work, and exits. It never runs continuously.

### Build
- Cloud Run Job Launcher service (triggered by Eventarc)
- Parse `OBJECT_FINALIZE` CloudEvent — extract `job_id`, `file_path`, `file_size_bytes`
- Read job metadata from Firestore
- Stream-validate `prompts.jsonl` line by line (never load full file into memory)
  - Valid line → `prompt_count++`
  - Invalid line → write to `gs://promptforge-output/{job_id}/errors.jsonl` immediately
  - Invalid format (not JSONL at all) → set `status = FAILED` and exit
- Check if another job for same `api_key_hash` is already `QUEUED` or `PROCESSING`
  - Yes → set this job `status = PENDING`, exit (no pod created)
  - No → update Firestore + create GKE Job
- Firestore update: `status = QUEUED`, `uploaded_at`, `queued_at`, `prompt_count`, `invalid_count`, `file_size_bytes`
- Create GKE Job with env vars injected: `JOB_ID`, `PROVIDER`, `MODEL`, `RPM_LIMIT`, `TPM_LIMIT`, `MAX_RETRIES`, `API_KEY_REF`, `INPUT_BUCKET`, `OUTPUT_BUCKET`, `PROMPTS_PATH`, `PROMPT_COUNT`
- Job Launcher exits after creating the pod

### Input
```
Phase 1: GCS bucket, Eventarc trigger, GKE cluster, Firestore
Phase 2: Firestore /jobs/{job_id} with status = AWAITING_UPLOAD
         prompts.jsonl uploaded to GCS input bucket
```

### Output
```
Firestore /jobs/{job_id}  status = QUEUED
GKE Job created and pod scheduled
Invalid lines written to errors.jsonl (if any)
```

### Verified when
- Upload a valid `prompts.jsonl` → Firestore status changes to `QUEUED` within seconds
- GKE pod is scheduled (`kubectl get pods`)
- Upload a file with some invalid lines → those lines appear in `errors.jsonl`, valid lines still proceed
- Upload a second job for the same client while one is `QUEUED` → second job gets `status = PENDING`, no pod created
- Upload a completely non-JSONL file → `status = FAILED`

---

## Phase 4 — Execution Engine: Baseline

### What it is
The core of the system. A GKE pod that reads prompts from GCS, calls the LLM, and collects responses — without any rate learning, buffering, or checkpointing yet. Just the dispatch loop + response handler in their most minimal form. This phase proves the execution contract end to end.

### Build
- GKE pod entrypoint — reads all env vars, fetches provider API key from Secret Manager
- Initialize in-memory state: `offset`, `completed`, `failed`, `running`, `retry_queue`
- **Dispatch Loop**
  - Open byte-range stream on `prompts.jsonl` in GCS — read one line at a time
  - Priority: `retry_queue` first, then next line from stream (`offset++`)
  - Dispatch each prompt as `asyncio.create_task(call_llm(...))`; `running += 1`
  - Fixed rate for now (no learning): use `RPM_LIMIT` directly, space requests evenly: `interval = 60 / RPM_LIMIT`
- **Response Handler**
  - `200 OK` → `running -= 1`, `completed += 1`, write result directly to GCS (one file per response, no buffer yet)
  - `429 / 5xx / timeout` → `running -= 1`, `prompt.attempt += 1`; if under `MAX_RETRIES` push to `retry_queue`, else `failed += 1`
  - `400 / 401 / 403` → `running -= 1`, `failed += 1` immediately (non-retryable)
- Update Firestore: `status = PROCESSING`, `started_processing_at = now()`
- Loop exits when `offset == EOF AND running == 0 AND retry_queue == []`
- Final Firestore update: `status = COMPLETED`

### Input
```
Phase 3: GKE Job scheduled, env vars injected, prompts.jsonl in GCS
Phase 1: Secret Manager, GCS output bucket
```

### Output
```
LLM responses written to GCS (one file per response at this stage)
Firestore /jobs/{job_id}  status = COMPLETED
completed + failed counts match total prompt_count
```

### Verified when
- Submit 20 prompts → all responses land in GCS → Firestore shows `COMPLETED`
- Submit a prompt with a bad `prompt_id` format → it lands in `errors.jsonl`
- Kill the pod mid-run → job stalls (no recovery yet, expected — recovery comes in Phase 7)
- Verify memory stays flat regardless of file size (stream, don't load)

---

## Phase 5 — Result Buffer & Storage

### What it is
Replace the per-response GCS write from Phase 4 with a batched buffer. This is a pure internal optimization — the external contract does not change.

### Build
- Add `result_buffer = []` and `error_buffer = []` to execution state
- On successful response: `result_buffer.append(record)` instead of writing to GCS
- **Flush conditions** (any one triggers a flush):
  - 500 responses accumulated
  - 50 MB of buffered data
  - 30 seconds since last flush
- On flush: serialize buffer to JSONL → upload to `results_part_NNN.jsonl` → `result_buffer.clear()`
- Part files numbered sequentially: `results_part_001.jsonl`, `results_part_002.jsonl`, ...
- Error buffer follows same flush strategy → appends to `errors.jsonl`
- On job completion: flush remaining buffer → `results_final.jsonl`

### Input
```
Phase 4: Working execution engine with direct GCS writes
```

### Output
```
GCS output bucket:
  results_part_001.jsonl   (up to 500 responses each)
  results_part_002.jsonl
  ...
  results_final.jsonl      (remainder at completion)
  errors.jsonl             (all failed prompts)
```

### Verified when
- Submit 1,200 prompts → exactly 3 part files (part_001: 500, part_002: 500, final: 200)
- Verify GCS write count is ~3, not 1,200
- Force a 30s flush interval → partial buffer flushed on time
- Error prompts land in `errors.jsonl` with full context: `prompt_id`, `attempts`, `last_error_code`

---

## Phase 6 — Rate Learning

### What it is
Replace the fixed `RPM_LIMIT` dispatch rate from Phase 4 with a dynamic, self-learning rate controller. The system discovers real provider limits the same way TCP discovers network capacity — no manual configuration required.

### Build
- Initialize: `rpm_target = 10`, `learning_mode = "slow_start"`, `p95_tokens = 1000`, `token_history = []`
- **Effective RPM calculation** (accounts for both request rate and token budget):
  ```
  effective_rpm = min(rpm_target, tpm_limit / p95_tokens)
  interval = 60 / effective_rpm
  ```
- **Slow Start** — before any 429:
  - Every 30 seconds: `rpm_target *= 1.5`
  - Progression: `10 → 15 → 22 → 33 → 49 → 73 → 110 ...`
- **On first 429**:
  - `dispatch_enabled = False` — no new requests leave
  - Drain in-flight: `while running > 0: await sleep(0.1)`
  - `rpm_target = rpm_target * 0.75`
  - `cooldown_until = now + 60s`; sleep until cooldown expires
  - `learning_mode = "congestion_avoidance"`, `dispatch_enabled = True`
- **Congestion Avoidance** — after first 429:
  - Every 30 seconds with no 429: `rpm_target += 1`
  - Progression: `375 → 376 → 377 ...`
  - Another 429 → same backoff cycle repeats from current `rpm_target`
- **TPM Learning** — after every successful response:
  ```
  token_history.append(actual_tokens)
  p95_tokens = percentile(token_history[-1000:], 95)
  ```
  - `effective_rpm` recalculated on every dispatch — TPM ceiling auto-adjusts as response sizes change

### Input
```
Phase 4/5: Working execution engine with fixed rate
```

### Output
```
rpm_target: dynamically learned, not manually set
p95_tokens: rolling P95, updated after every response
effective_rpm: respects whichever limit (RPM or TPM) is tighter
No 429s reach the error log — they are absorbed by the learning loop
```

### Verified when
- Start a job against a sandboxed mock provider with RPM=100 — system converges to ~100 without being told
- Simulate a 429 at `rpm_target=500` → `rpm_target` drops to 375, cooldown fires, dispatch resumes
- Send prompts with varying response lengths → `p95_tokens` rises → `effective_rpm` decreases automatically
- Verify `rpm_target` never exceeds user-supplied `RPM_LIMIT`

---

## Phase 7 — Checkpointing & Recovery

### What it is
Make the execution engine crash-proof. State is persisted to GCS periodically. If the pod dies, Kubernetes restarts it and it picks up from where it left off. At-least-once delivery is the guarantee.

### Build
- **Checkpoint Writer** (runs concurrently with all other loops):
  - Every 30 seconds, or immediately after every result buffer flush, write `state.json` to GCS:
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
  - Always overwrite (not append) — one file, always the latest known-good state
- **Startup recovery** — before beginning dispatch:
  - Check GCS for existing `state.json`
  - If found: restore `offset`, `rpm_target`, `tpm_target`, `p95_tokens`; log `checkpoint_restored`
  - If not found: start fresh
- **SIGTERM handler** — Kubernetes sends this before killing a pod:
  ```python
  signal.signal(signal.SIGTERM, handle_sigterm)
  # sets dispatch_enabled = False
  # in-flight requests drain naturally
  # checkpoint writer fires one final time
  # pod exits cleanly
  ```
- Accept at-least-once: prompts processed after the last checkpoint but before a crash will be re-sent. This is intentional — duplicate work is preferred over lost work.

### Input
```
Phase 5: Result buffer + GCS write pipeline
Phase 6: Rate learning state (rpm_target, p95_tokens to persist)
```

### Output
```
GCS: state.json  (overwritten every 30s)
Pod crash → Kubernetes restart → resumes from last offset
SIGTERM → clean drain → no data loss
```

### Verified when
- Kill the pod at `offset=50000` → new pod starts → reads `state.json` → resumes from `offset=49500` (last checkpoint)
- Some prompts near the boundary appear twice in results (expected — at-least-once)
- Send SIGTERM to pod → dispatch stops, in-flight drains, final checkpoint written, pod exits with code 0
- Pod with no prior `state.json` starts fresh (cold start path works)

---

## Phase 8 — Job Completion & Reconciliation

### What it is
The job lifecycle close. Detecting completion, verifying nothing was lost, cleaning up inputs, and triggering the next queued job for the same client.

### Build
- **Completion condition** checked after every counter update:
  ```
  offset == EOF AND running == 0 AND retry_queue == []
  ```
- **Final flush sequence** (in order):
  1. Flush `result_buffer` → `results_final.jsonl` → GCS
  2. Flush `error_buffer` → `errors.jsonl` → GCS
  3. Write final `state.json` → GCS
  4. Run reconciliation pass
  5. Delete `prompts.jsonl` from input bucket
  6. Firestore: `status = COMPLETED`, `completed_at = now()`
  7. Check Firestore for oldest `PENDING` job with same `api_key_hash`
     - If found: set it to `QUEUED`, create GKE Job for it → next job begins automatically
  8. Pod exits
- **Reconciliation pass** — bitset over all `prompt_id` values:
  ```python
  seen = bitarray(PROMPT_COUNT + 1)
  # stream all results_part_*.jsonl + results_final.jsonl
  # stream errors.jsonl
  # mark seen[prompt_id] = 1 for every record found
  missing = [i for i in range(1, PROMPT_COUNT + 1) if not seen[i]]
  # log each missing ID as WARNING to OTel — no retry, just surfaced
  ```
  - 10M prompts = 1.25 MB bitset — memory cost is negligible
  - Job still completes as `COMPLETED` even if missing IDs are found

### Input
```
Phase 5: result_buffer, error_buffer, GCS part files exist
Phase 7: state.json, checkpoint writer, SIGTERM handling
Phase 3: PENDING job queue logic in Firestore
```

### Output
```
Firestore /jobs/{job_id}  status = COMPLETED
GCS input bucket: prompts.jsonl deleted
GCS output bucket: results_final.jsonl, errors.jsonl, state.json written
Missing prompt IDs logged to OTel (if any)
Next queued job triggered automatically (if one exists)
```

### Verified when
- Run a full job → `status = COMPLETED`, all result files present, input file deleted
- Inject a synthetic gap in results (skip writing `prompt_id=50`) → reconciliation logs it as WARNING
- Queue two jobs → first completes → second starts automatically within seconds
- Verify bitset memory usage stays flat for 10M-prompt jobs

---

## Phase 9 — Status & Results API

### What it is
The client-facing read path. Three endpoints that let a client poll job progress, download results, and cancel a running job. These are read-heavy and stateless — they query Firestore and GCS, do nothing else.

### Build
- **`GET /v1/jobs/{job_id}`** — job status and counters
  - Read Firestore `/jobs/{job_id}`
  - Return: `status`, `total`, `completed`, `failed`, `pending`, `progress_pct`, `created_at`, `estimated_completion`
  - `404` if job not found or belongs to a different `client_id`
  - `401` on invalid API key
- **`GET /v1/jobs/{job_id}/results`** — signed download URLs
  - Only available when `status = completed`; returns `409` if still running
  - List all `results_part_*.jsonl` + `results_final.jsonl` in output bucket
  - Generate signed GCS URLs (1hr expiry) for each file
  - Return: `result_urls[]`, `error_urls[]`, `expires_at`
- **`DELETE /v1/jobs/{job_id}`** — cancel a running job
  - Firestore: `status = CANCELLED`
  - Pod detects cancellation on next dispatch loop iteration (checks Firestore before each dispatch)
  - Prompts already in-flight complete naturally; pending prompts are abandoned
  - Returns `409` if job already completed or cancelled

### Input
```
Phase 2: API Service infrastructure (Cloud Run, auth middleware)
Phase 8: Completed jobs with results in GCS and Firestore
Phase 4: Running jobs with status in Firestore
```

### Output
```
GET /v1/jobs/{job_id}           →  live job progress
GET /v1/jobs/{job_id}/results   →  signed URLs to download JSONL files
DELETE /v1/jobs/{job_id}        →  job cancelled
```

### Verified when
- Submit a job → poll `GET /jobs/{id}` → watch `progress_pct` climb in real time
- Wait for completion → `GET /jobs/{id}/results` → download all URLs → merge JSONL → verify prompt count matches
- `GET /jobs/{id}/results` on a running job → returns `409`
- `DELETE /jobs/{id}` on a running job → Firestore status flips → pod stops dispatching new prompts
- `GET /jobs/{id}` with wrong API key → `404` (not 403 — do not reveal existence)

---

## Phase 10 — Observability

### What it is
Full visibility into every job, every prompt, and the system's health over time. OpenTelemetry across all services, exportable to any backend without code changes.

### Build
- **OTel SDK setup** in all three services (API, Job Launcher, Execution Pod)
  - Export target configurable via env var (`OTEL_EXPORTER_OTLP_ENDPOINT`)
  - Backend: **Axiom** for logs + traces, **Sentry** for error tracking, **Better Stack** for uptime monitoring
  - Switching backend = one env var change. No application code changes.
- **Traces** — job as root span, every prompt as child:
  ```
  Trace: job_123
    ├── span: job.init
    ├── span: job.launch          (file validated, pod created)
    ├── span: job.execute
    │     ├── span: prompt.dispatch  [prompt_id, attempt, latency_ms, tokens]
    │     ├── span: buffer.flush     [batch_number, response_count]
    │     └── span: checkpoint.write [offset, rpm_target, p95_tokens]
    └── span: job.complete
  ```
- **Metrics** (key gauges and counters):
  ```
  promptforge.rate.rpm_target        gauge
  promptforge.rate.effective_rpm     gauge
  promptforge.rate.p95_tokens        gauge
  promptforge.rate.429_events        counter
  promptforge.prompts.completed      counter
  promptforge.prompts.failed         counter
  promptforge.llm.latency_ms         histogram
  promptforge.queue.retry_depth      gauge
  promptforge.buffer.size            gauge
  ```
- **Structured logs** — JSON, consistent schema across all services:
  ```json
  {
    "timestamp": "...", "level": "WARN", "event": "rate_429_received",
    "job_id": "...", "prompt_id": 47, "attempt": 2,
    "provider": "openai", "model": "gpt-4o",
    "error_code": 429, "rpm_target": 375, "p95_tokens": 850
  }
  ```
- **Key log events**: `job_started`, `checkpoint_restored`, `prompt_retried`, `prompt_failed`, `rate_429_received`, `rate_backoff_applied`, `buffer_flushed`, `checkpoint_written`, `job_completed`, `sigterm_received`, `pod_recovering`
- `prompt_id` is NEVER used as a metric label (unbounded cardinality) — traces and logs only

### Input
```
All previous phases — instrumented at source
```

### Output
```
Every prompt: a trace span with full token + latency data (Axiom)
Every rate event: a metric data point in real time (Axiom)
Every state change: a structured log line (Axiom)
Every unhandled exception: captured with stack trace (Sentry)
API endpoint uptime monitored externally (Better Stack)
```

### Verified when
- Submit a job → open Axiom → see `rpm_target` climb in real time
- Find a failed prompt by `prompt_id` in Axiom traces → see all 3 retry attempts as child spans
- Simulate a 429 → `rate.429_events` counter increments → alert fires in Axiom
- Kill a pod with an unhandled exception → error appears in Sentry with full stack trace
- Take down the API → Better Stack fires an alert within 3 minutes
- Confirm no `prompt_id` label on any metric (cardinality check)

---

## Integration Order

```
Phase 1   ──────────────────────────────────────  infrastructure ready
    ↓
Phase 2   ──────────────────────────────────────  clients can submit jobs
    ↓
Phase 3   ──────────────────────────────────────  uploads trigger execution
    ↓
Phase 4   ──────────────────────────────────────  prompts reach the LLM
    ↓
Phase 5   ──────────────────────────────────────  results land in GCS efficiently
    ↓
Phase 6   ──────────────────────────────────────  rate limits respected automatically
    ↓
Phase 7   ──────────────────────────────────────  pod crashes are invisible
    ↓
Phase 8   ──────────────────────────────────────  jobs complete cleanly, nothing lost
    ↓
Phase 9   ──────────────────────────────────────  clients can poll and download
    ↓
Phase 10  ──────────────────────────────────────  full visibility across the system
```

At the end of Phase 8, PromptForge is functionally complete. Phase 9 adds the client-facing read API. Phase 10 adds production-grade observability. Both can be developed in parallel with Phase 8 once Phase 4 is stable.
