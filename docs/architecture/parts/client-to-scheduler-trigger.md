```text
┌──────────────────────────────────────────────────────────────────────┐
│                         PHASE 1 : JOB INIT                           │
└──────────────────────────────────────────────────────────────────────┘

User
 │
 │ POST /v1/jobs/init
 │
 ▼
Cloud Run API
 │
 ├── Generate job_id
 │
 ├── Store provider/model/rate limits
 │
 ├── Store upload_path
 │
 ├── Store api_key_ref (Secret Manager)
 │
 ├── Create Firestore Record
 │
 ▼
Firestore

/jobs/{job_id}

{
  job_id,
  status: "AWAITING_UPLOAD",

  provider,
  model,

  api_key_ref,

  rpm,
  tpm,

  max_retries,

  upload_path,

  prompt_count: null,
  file_size_bytes: null,

  created_at,
  uploaded_at: null,
  queued_at: null,
  started_processing_at: null
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
 │ PUT prompts.jsonl
 │
 ▼
GCS Bucket

gs://bucket/uploads/{job_id}/prompts.jsonl

 │
 │ Upload completes
 │
 ▼
OBJECT_FINALIZE Event


┌──────────────────────────────────────────────────────────────────────┐
│                    PHASE 3 : UPLOAD FINALIZATION                     │
└──────────────────────────────────────────────────────────────────────┘

OBJECT_FINALIZE
 │
 ▼
Eventarc
 │
 ▼
Cloud Run Function

Reads:
  job_id
  file_path
  file_size

Updates Firestore

/jobs/{job_id}

{
  status: "QUEUED",

  uploaded_at: now(),
  queued_at: now(),

  file_size_bytes: xxx,

  prompt_count: optional
}


┌──────────────────────────────────────────────────────────────────────┐
│                    PHASE 4 : JOB DISCOVERY                           │
└──────────────────────────────────────────────────────────────────────┘

Firestore
 │
 │ watch(status == "QUEUED")
 │
 ▼
GKE Scheduler

Receives:

{
  job_id,

  provider,
  model,

  rpm,
  tpm,

  api_key_ref,

  upload_path,

  prompt_count,

  file_size_bytes
}

Scheduler now has:

✓ job metadata
✓ upload location
✓ model information
✓ rate limits
✓ secret reference

and can start execution planning.


┌──────────────────────────────────────────────────────────────────────┐
│                         DATA OWNERSHIP                               │
└──────────────────────────────────────────────────────────────────────┘

Firestore
 ├── Job metadata
 ├── Job status
 ├── Timestamps
 ├── Rate limits
 └── Secret references

GCS
 ├── prompts.jsonl
 ├── results.jsonl
 └── errors.jsonl

Secret Manager
 └── Actual provider API keys

GKE Scheduler
 └── Watches Firestore only

No bucket polling.
No prompt storage in Firestore.
No periodic database polling.
Fully event-driven until scheduler.
```
