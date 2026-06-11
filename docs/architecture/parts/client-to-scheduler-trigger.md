```text
┌──────────────────────────────────────────────────────────────────────┐
│                         PHASE 1 : JOB INIT                           │
└──────────────────────────────────────────────────────────────────────┘

User
 │
 │ POST /v1/jobs/init
 │ X-API-Key: {api_key}
 │
 ▼
Cloud Run API
 │
 ├── Verify API key via Secret Manager
 │     Loads JSON dict from `promptforge-api-keys` secret (5-min cache)
 │     valid  → resolve client_id from dict value
 │     invalid → 401, stop
 │
 ├── Generate job_id
 │
 ├── Store provider / model / rate limits
 │   (RPM/TPM defaults filled from Tier 1 limits if not provided)
 │
 ├── Store api_key_ref (Secret Manager path for provider LLM key)
 │
 ├── Store upload_path
 │
 ├── Create Firestore Record
 │
 ▼
Firestore

/jobs/{job_id}

{
  job_id,
  client_id,            ← resolved from Secret Manager key lookup

  status: "AWAITING_UPLOAD",

  provider,
  model,

  api_key_ref,          ← Secret Manager path, not the key itself

  rpm,
  tpm,

  max_retries,

  upload_path,          ← {client_id}/{job_id}/prompts.jsonl

  prompt_count: null,
  invalid_count: null,
  file_size_bytes: null,

  created_at,
  uploaded_at: null,
  queued_at: null,
  started_processing_at: null,
  completed_at: null
}

 ▲
 │
 └── Return Signed Upload URL
      +
      job_id

 ▼
User


┌──────────────────────────────────────────────────────────────────────┐
│                         PHASE 2 : FILE UPLOAD                        │
└──────────────────────────────────────────────────────────────────────┘

User
 │
 │ PUT prompts.jsonl  (direct to GCS, API never touches file contents)
 │
 ▼
GCS Input Bucket

gs://promptforge-input/{client_id}/{job_id}/prompts.jsonl

 │
 │ Upload completes
 │
 ▼
OBJECT_FINALIZE Event


┌──────────────────────────────────────────────────────────────────────┐
│              PHASE 3 : UPLOAD FINALIZATION & POD CREATION            │
└──────────────────────────────────────────────────────────────────────┘

OBJECT_FINALIZE
 │
 ▼
Eventarc
 │
 ▼
Cloud Run Job Launcher

Reads from event:
  job_id
  file_path
  file_size_bytes

Reads from Firestore:
  provider, model, api_key_ref, rpm, tpm, max_retries

Stream-validates prompts.jsonl line by line (never loaded in full):
  valid line   → prompt_count++
  invalid line → written to gs://promptforge-output/{client_id}/{job_id}/errors.jsonl
  not JSONL    → status = FAILED, exit

Checks Firestore:
  another job for same client_id already QUEUED or PROCESSING?
    yes → status = PENDING, exit (no pod created)
    no  → continue

Updates Firestore

/jobs/{job_id}

{
  status: "QUEUED",

  uploaded_at: now(),
  queued_at: now(),

  file_size_bytes: xxx,
  prompt_count: xxx,
  invalid_count: xxx
}

Creates GKE Job with env vars:

  JOB_ID        = job_123
  CLIENT_ID     = {client_id}
  PROVIDER      = openai
  MODEL         = gpt-4o-mini
  RPM_LIMIT     = 500
  TPM_LIMIT     = 200000
  MAX_RETRIES   = 3
  API_KEY_REF   = projects/promptforge-1212/secrets/openai-api-key/versions/latest
  PROMPTS_PATH  = {client_id}/job_123/prompts.jsonl
  PROMPT_COUNT  = 94500
  INPUT_BUCKET  = promptforge-input-promptforge-1212
  OUTPUT_BUCKET = promptforge-output-promptforge-1212

Job Launcher exits.


┌──────────────────────────────────────────────────────────────────────┐
│                    PHASE 4 : EXECUTION POD STARTUP                   │
└──────────────────────────────────────────────────────────────────────┘

GKE Execution Pod starts
 │
 ├── Read all env vars
 │
 ├── Fetch provider api_key from Secret Manager using API_KEY_REF
 │     (key held in memory only, never logged)
 │
 ├── Initialize in-memory execution state
 │     offset, completed, failed, running
 │     retry_queue, result_buffer, token_history
 │     rpm_target=10, tpm_target=TPM_LIMIT, p95_tokens=1000
 │     dispatch_enabled=True, learning_mode="slow_start"
 │
 ├── Check GCS for state.json
 │     found     → restore checkpoint (crash recovery path)
 │     not found → cold start
 │
 ├── Update Firestore: status = PROCESSING, started_processing_at = now()
 │
 └── Start four concurrent loops:
       Dispatch Loop
       Response Handler
       Result Buffer
       Checkpoint Writer


┌──────────────────────────────────────────────────────────────────────┐
│                         DATA OWNERSHIP                               │
└──────────────────────────────────────────────────────────────────────┘

Secret Manager
 ├── Client API keys (`promptforge-api-keys` secret — JSON dict api_key → client_id)
 └── Provider LLM API keys (one secret per provider: openai-api-key, gemini-api-key)

Firestore
 ├── Job metadata
 ├── Job status
 ├── Timestamps
 ├── Rate limits
 ├── api_key_ref (Secret Manager path, not the key itself)
 └── client_id

GCS — Input Bucket (promptforge-input-promptforge-1212)
 └── {client_id}/{job_id}/prompts.jsonl
       Deleted by execution pod on job completion

GCS — Output Bucket (promptforge-output-promptforge-1212)
 ├── {client_id}/{job_id}/results_part_NNN.jsonl
 ├── {client_id}/{job_id}/results_final.jsonl
 ├── {client_id}/{job_id}/errors.jsonl
 └── {client_id}/{job_id}/state.json

GKE Execution Pod (in-memory only)
 └── All rate learning state, buffers, counters

No bucket polling.
No prompt storage in Firestore.
No periodic database polling.
Fully event-driven from upload to pod creation.
```
