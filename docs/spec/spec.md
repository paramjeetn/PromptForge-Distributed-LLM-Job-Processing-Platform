# PromptForge — System Specification

Version: 1.0  
Status: Draft  
Platform: Google Cloud

---

## 1. API Specification

### Base URL
```
https://api.promptforge.io/v1
```

### Authentication
All requests require the client's API key in the header:
```
X-API-Key: {api_key}
```

The API layer hashes this key to derive `client_id`. The raw key is never stored.

---

### POST /jobs

Submit a new job.

**Request body:**
```json
{
  "provider": "openai | gemini | anthropic",
  "model": "string",
  "prompts": ["string", "..."],
  "max_retries": 3,
  "rpm": null,
  "tpm": null
}
```

| Field | Required | Description |
|---|---|---|
| `provider` | Yes | LLM provider |
| `model` | Yes | Model identifier (e.g. `gpt-4o`, `gemini-1.5-flash`) |
| `prompts` | Yes | Array of prompt strings, any length |
| `max_retries` | No | Default 3. Per-prompt retry limit for transient errors |
| `rpm` | No | Client-supplied RPM cap. Effective = min(provider_default, this) |
| `tpm` | No | Client-supplied TPM cap. Effective = min(provider_default, this) |

**Response 202 Accepted:**
```json
{
  "job_id": "uuid",
  "status": "queued",
  "total_prompts": 1000,
  "created_at": "ISO8601"
}
```

**Error responses:**

| Code | Reason |
|---|---|
| `400` | Invalid payload, missing required fields |
| `401` | Invalid or missing API key |
| `422` | Unsupported provider or model |

---

### GET /jobs/{job_id}

Get current job status and progress.

**Response 200 OK:**
```json
{
  "job_id": "uuid",
  "status": "queued | running | completed | failed | cancelled",
  "total": 1000,
  "pending": 400,
  "running": 50,
  "completed": 530,
  "failed": 20,
  "progress_pct": 55.0,
  "created_at": "ISO8601",
  "started_at": "ISO8601",
  "estimated_completion": "ISO8601"
}
```

**Error responses:**

| Code | Reason |
|---|---|
| `401` | Invalid API key |
| `404` | Job not found or does not belong to this client |

---

### DELETE /jobs/{job_id}

Cancel a running job.

**Response 200 OK:**
```json
{
  "job_id": "uuid",
  "status": "cancelled"
}
```

Prompts already delivered to workers finish naturally. Pending prompts are abandoned. Cloud Tasks queue for this client is purged of this job's tasks.

**Error responses:**

| Code | Reason |
|---|---|
| `404` | Job not found |
| `409` | Job already completed or cancelled |

---

### GET /jobs/{job_id}/results

Get signed download URLs for completed results.

**Response 200 OK:**
```json
{
  "job_id": "uuid",
  "status": "completed",
  "result_urls": ["https://signed-gcs-url", "..."],
  "error_urls": ["https://signed-gcs-url", "..."],
  "expires_at": "ISO8601"
}
```

URLs are time-limited signed GCS URLs (default expiry: 1 hour). Each URL points to one JSONL part file. Clients merge part files to reconstruct the full result set.

Only available when job status = completed. Returns 409 if job is still running.

---

## 2. Data Specification

### Firestore Collections

#### jobs/{job_id}

| Field | Type | Description |
|---|---|---|
| `job_id` | string | UUID, immutable |
| `client_id` | string | SHA-256 hash of API key |
| `provider` | string | openai / gemini / anthropic |
| `model` | string | Model identifier |
| `status` | string | QUEUED / RUNNING / COMPLETED / FAILED / CANCELLED |
| `total` | int | Total prompt count |
| `pending` | int | Prompts awaiting dispatch |
| `running` | int | Prompts currently with a worker |
| `completed` | int | Successfully completed prompts |
| `failed` | int | Permanently failed prompts |
| `max_retries` | int | Per-prompt retry limit |
| `created_at` | timestamp | |
| `started_at` | timestamp | When first prompt was dispatched |
| `completed_at` | timestamp | When last prompt reached terminal state |

---

#### prompts/{job_id}/{prompt_id}

| Field | Type | Description |
|---|---|---|
| `prompt_id` | string | UUID, immutable |
| `job_id` | string | Parent job reference |
| `client_id` | string | |
| `prompt_text` | string | Original prompt content |
| `status` | string | PENDING / RUNNING / COMPLETED / FAILED |
| `retry_count` | int | Incremented on transient errors only |
| `running_since` | timestamp | Set when moved to RUNNING, used by stale sweep |
| `estimated_tokens` | int | Pre-dispatch estimate (input + P95 output) |
| `actual_input_tokens` | int | From provider response metadata |
| `actual_output_tokens` | int | From provider response metadata |
| `error_message` | string | Set on FAILED prompts |
| `completed_at` | timestamp | |

---

#### quota/{client_id}

| Field | Type | Description |
|---|---|---|
| `client_id` | string | |
| `provider` | string | |
| `model` | string | |
| `rpm_limit` | int | Effective RPM ceiling |
| `tpm_limit` | int | Effective TPM ceiling |
| `rpm_used` | int | Requests dispatched in current window |
| `tpm_used` | int | Tokens consumed in current window |
| `window_start` | timestamp | Start of current 60s window |
| `p95_output_tokens` | int | Rolling P95 estimate of output tokens |
| `p95_sample_count` | int | Number of completions contributing to P95 |
| `cooldown_until` | timestamp | No dispatches before this time |
| `last_429_at` | timestamp | Most recent rate limit error |

---

#### model_defaults/{provider}/{model}

| Field | Type | Description |
|---|---|---|
| `tier1_rpm` | int | Provider's published tier-1 RPM limit |
| `tier1_tpm` | int | Provider's published tier-1 TPM limit |
| `p95_seed_tokens` | int | Cold-start P95 estimate before any history exists |

---

### GCS Layout

```
gs://{bucket}/
  jobs/{client_id}/{job_id}/request.json
  results/{job_id}/part_{prompt_id}.jsonl
  errors/{job_id}/error_{prompt_id}.json
```

**Result record schema (JSONL):**
```json
{
  "prompt_id": "uuid",
  "job_id": "uuid",
  "prompt_text": "string",
  "response_text": "string",
  "model": "string",
  "provider": "string",
  "actual_input_tokens": 120,
  "actual_output_tokens": 340,
  "latency_ms": 1840,
  "completed_at": "ISO8601"
}
```

**Error record schema (JSON):**
```json
{
  "prompt_id": "uuid",
  "job_id": "uuid",
  "prompt_text": "string",
  "error_code": "string",
  "error_message": "string",
  "retry_count": 3,
  "last_attempted_at": "ISO8601"
}
```

---

## 3. Prompt State Machine

```
PENDING → RUNNING → COMPLETED
               ↓
            PENDING  (429 or transient error, retries remain)
               ↓
            FAILED   (terminal error or retries exhausted)
```

| Transition | Trigger | retry_count |
|---|---|---|
| PENDING → RUNNING | Scheduler dispatch | unchanged |
| RUNNING → COMPLETED | Worker: LLM success | unchanged |
| RUNNING → PENDING | Worker: 429 received | unchanged |
| RUNNING → PENDING | Worker: transient error, retries remain | +1 |
| RUNNING → PENDING | Stale sweep: running_since > 120s | unchanged |
| RUNNING → FAILED | Worker: terminal error (4xx != 429) | unchanged |
| RUNNING → FAILED | Worker: transient error, retries exhausted | +1 |

---

## 4. Scheduler Specification

**Runtime:** Single GKE pod, always-on.
**Concurrency:** One async dispatch loop coroutine per active client.
**Recovery:** On pod restart, queries Firestore for clients with pending > 0 and resumes their loops.

### Dispatch Loop (per client, per iteration)

```
1. Cancellation check
   For each job: if status = CANCELLED, skip its pending prompts

2. Stale recovery
   Query: status = RUNNING AND running_since < now - 120s
   Action: reset to PENDING (retry_count unchanged)

3. Cooldown check
   If now < quota.cooldown_until → sleep until expiry, restart iteration

4. Window reset
   If now - quota.window_start > 60s → reset rpm_used = 0, tpm_used = 0

5. Quota calculation
   available_rpm = rpm_limit - rpm_used
   available_tpm = tpm_limit - tpm_used
   n = min(available_rpm, floor(available_tpm / p95_output_tokens), MAX_DISPATCH)
   If n = 0 → sleep until next window reset

6. Fetch prompts
   Query Firestore: status = PENDING, order by created_at ASC, limit n

7. Atomic Firestore batch write
   Set each prompt: status = RUNNING, running_since = now
   Increment quota: rpm_used += n, tpm_used += sum(estimated_tokens)

8. Enqueue to Cloud Tasks
   One task per prompt on queue-{client_id}

9. Sleep 1s → next iteration
```

**Loop exit condition:** exits when `pending + running == 0` for all jobs under this client.

---

## 5. Worker Specification

**Runtime:** Cloud Run, autoscaling, stateless.
**Invocation:** HTTP POST from Cloud Tasks.
**Concurrency:** One prompt per invocation.

### Execution contract

```
1. Parse task payload
2. Fetch LLM provider key from Secret Manager
3. Call LLM provider API
4. Handle response:

   SUCCESS (2xx)
   - Write result to GCS: results/{job_id}/part_{prompt_id}.jsonl
   - Firestore: prompt status = COMPLETED
   - Firestore: job.completed++, job.running--
   - Firestore: update quota p95_output_tokens with actual output tokens
   - Emit OTEL span

   RATE LIMITED (429)
   - Firestore: prompt status = PENDING (retry_count unchanged)
   - Firestore: quota.cooldown_until = now + 60s
   - Cloud Tasks: pause queue-{client_id}
   - Emit OTEL span

   TRANSIENT ERROR (5xx, timeout)
   - retry_count++
   - If retry_count < max_retries → Firestore: prompt status = PENDING
   - If retry_count >= max_retries → FAILED + GCS error record

   TERMINAL ERROR (4xx except 429)
   - Firestore: prompt status = FAILED immediately
   - GCS: write error record

5. Return HTTP 200 to acknowledge Cloud Tasks delivery
```

**Job completion check:** after every counter update, worker checks `if completed + failed == total → job status = COMPLETED`.

---

## 6. Quota and Token Estimation

### Effective limit
```
effective_rpm = min(tier1_rpm, client_rpm)
effective_tpm = min(tier1_tpm, client_tpm)
```

### Token estimation
```
estimated_tokens = tokenizer.count(prompt_text) + quota.p95_output_tokens
```

### P95 update
Updated after every successful completion using an online algorithm. No historical data stored — only the running P95 and sample count.

### Cold start
When `p95_sample_count == 0`, use `model_defaults.p95_seed_tokens`. Conservative by design.

---

## 7. Error Classification

| HTTP Code | Class | retry_count | Behaviour |
|---|---|---|---|
| 2xx | Success | — | Complete prompt |
| 429 | Quota | unchanged | PENDING + cooldown |
| 500, 502, 503, 504 | Transient | +1 | PENDING if retries remain, else FAILED |
| 408, timeout | Transient | +1 | PENDING if retries remain, else FAILED |
| 400, 401, 403, 422 | Terminal | unchanged | FAILED immediately |

---

## 8. Security Specification

| Concern | Implementation |
|---|---|
| Client identity | API key hashed SHA-256 + server salt → client_id |
| LLM provider keys | GCP Secret Manager, fetched by workers at runtime |
| Task payloads | Never contain raw API keys |
| Result access | Signed GCS URLs, 1hr expiry |
| Data isolation | Firestore rules + GCS IAM scoped by client_id |
| Logs and traces | client_id used in all telemetry, never raw key |

---

## 9. Observability Specification

### Trace attributes (every prompt span)
```
job_id, client_id, prompt_id, provider, model,
retry_count, estimated_tokens, actual_tokens, latency_ms, status
```

### Key metrics

| Metric | Type | Description |
|---|---|---|
| `rpm_utilization` | Gauge | Fraction of RPM budget used |
| `tpm_utilization` | Gauge | Fraction of TPM budget used |
| `queue_depth` | Gauge | Pending prompts per client |
| `prompt_latency_ms` | Histogram | End-to-end prompt duration |
| `llm_latency_ms` | Histogram | Provider API call duration |
| `success_rate` | Gauge | completed / total |
| `failure_rate` | Gauge | failed / total |
| `429_total` | Counter | Rate limit events |
| `cooldown_events_total` | Counter | Cooldown periods entered |
| `tokens_input_total` | Counter | Cumulative input tokens |
| `tokens_output_total` | Counter | Cumulative output tokens |
| `estimated_cost_usd` | Gauge | Rolling cost estimate |

### Structured log fields (all services)
```
timestamp, service, severity, job_id, client_id, prompt_id,
provider, model, retry_count, latency_ms, error_code
```

### Alerting thresholds

| Alert | Condition |
|---|---|
| Quota pressure | rpm_utilization > 0.95 for > 2 min |
| Throttling | 429_total rate > 10/min |
| Scheduler stale | queue_depth > 0 with no scheduler activity for > 5 min |
| Worker error spike | failure_rate > 5% |

---

## 10. Technology Stack

| Component | Technology |
|---|---|
| API Layer | Cloud Run |
| Scheduler | GKE (single pod, always-on) |
| Task Queue | Cloud Tasks (per-client queues) |
| Workers | Cloud Run (autoscaling) |
| Operational State | Firestore |
| Object Storage | Google Cloud Storage |
| Secrets | GCP Secret Manager |
| Providers | OpenAI, Gemini, Anthropic |
| Tracing | OpenTelemetry → Tempo |
| Logs | OpenTelemetry → Loki |
| Metrics | OpenTelemetry → Mimir |
| Dashboards | Grafana |