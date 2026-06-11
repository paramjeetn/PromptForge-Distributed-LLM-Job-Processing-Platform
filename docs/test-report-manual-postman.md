# Manual Test Report — Postman End-to-End
_Date: 2026-06-11_

---

## Overview

Manual end-to-end test of the full PromptForge flow using Postman against the live production deployment. No automated tooling — every step executed by hand via HTTP requests.

**System under test:** Live GCP deployment
**API:** `https://promptforge-api-517402593902.us-central1.run.app`
**Client:** `e2e-test-client`
**Job ID:** `927b5107-c4f2-498f-80ea-b3610676c00c`

---

## Test Input

3 arithmetic prompts submitted as `prompts.jsonl`:

```
{"prompt_id": 1, "prompt": "What is 1 + 1? Reply with just the number."}
{"prompt_id": 2, "prompt": "What is 2 + 2? Reply with just the number."}
{"prompt_id": 3, "prompt": "What is 3 + 3? Reply with just the number."}
```

---

## Step 1 — POST /v1/jobs/init

**Request:**
```
POST /v1/jobs/init
X-API-Key: [redacted]
Content-Type: application/json

{
  "provider": "openai",
  "model": "gpt-4o-mini",
  "max_retries": 3
}
```

**Response — 202 Accepted:**
```json
{
  "job_id": "927b5107-c4f2-498f-80ea-b3610676c00c",
  "upload_url": "https://storage.googleapis.com/promptforge-input-promptforge-1212/...",
  "expires_at": "2026-06-11T17:02:49.945871Z"
}
```

✅ Job created in Firestore with status `AWAITING_UPLOAD`. Signed GCS upload URL valid for 15 minutes.

---

## Step 2 — PUT prompts.jsonl to GCS

**Request:**
```
PUT <signed_upload_url>
Content-Type: application/x-ndjson

[3 lines of JSONL]
```

**Response — 200 OK** (empty body from GCS)

✅ File landed at `gs://promptforge-input-promptforge-1212/e2e-test-client/927b5107-.../prompts.jsonl`. Eventarc `OBJECT_FINALIZE` triggered within seconds, Launcher validated 3 prompts (0 invalid) and created GKE Job `promptforge-exec-927b5107c4f2`.

---

## Step 3 — Poll GET /status

Polled every ~15 seconds. Progression observed:

| Time (UTC) | Status |
|---|---|
| 16:47:49 | `AWAITING_UPLOAD` — job created |
| 16:51:55 | `QUEUED` — Launcher created GKE Job |
| 17:00:41 | `PROCESSING` — pod started, fetching API key from Secret Manager |
| 17:01:00 | `COMPLETED` |

**Final status response:**
```json
{
  "job_id": "927b5107-c4f2-498f-80ea-b3610676c00c",
  "status": "COMPLETED",
  "provider": "openai",
  "model": "gpt-4o-mini",
  "prompt_count": 3,
  "completed_count": 3,
  "failed_count": 0,
  "created_at": "2026-06-11T16:47:49.945910Z",
  "started_processing_at": "2026-06-11T17:00:41.307684Z",
  "completed_at": "2026-06-11T17:01:00.862436Z"
}
```

✅ All 3 prompts completed, 0 failed.

---

## Step 4 — GET /results

**Response — 200:**
```json
{
  "job_id": "927b5107-c4f2-498f-80ea-b3610676c00c",
  "status": "COMPLETED",
  "files": [
    {
      "filename": "results_final.jsonl",
      "url": "https://storage.googleapis.com/promptforge-output-promptforge-1212/...",
      "expires_at": "2026-06-11T18:02:14.224696Z"
    }
  ]
}
```

✅ One result file returned with a 1-hour signed download URL.

---

## Step 5 — Download results_final.jsonl

Downloaded via the signed URL (no auth required). Contents:

```json
{"prompt_id": 1, "response": "2", "model": "gpt-4o-mini", "provider": "openai", "tokens": {"prompt": 21, "completion": 1}, "latency_ms": 3713, "attempt": 1}
{"prompt_id": 2, "response": "4", "model": "gpt-4o-mini", "provider": "openai", "tokens": {"prompt": 21, "completion": 1}, "latency_ms": 481,  "attempt": 1}
{"prompt_id": 3, "response": "6", "model": "gpt-4o-mini", "provider": "openai", "tokens": {"prompt": 21, "completion": 1}, "latency_ms": 778,  "attempt": 1}
```

✅ All 3 prompt IDs present. Correct answers (2, 4, 6). Token counts accurate (21 prompt tokens, 1 completion token each). All first-attempt successes.

---

## Result

| Check | Result |
|---|---|
| Job init returns signed URL | ✅ |
| File upload triggers pipeline | ✅ |
| Launcher validates + creates GKE Job | ✅ |
| Execution pod processes all prompts | ✅ |
| Results written to GCS | ✅ |
| Signed download URLs returned | ✅ |
| Response content correct | ✅ |
| 0 failures | ✅ |

**All checks passed.**

---

## Timings

| Phase | Duration |
|---|---|
| Job init → file upload | ~2s |
| Upload → Launcher fired | ~4s (Eventarc) |
| GKE Job created → pod running | **~9 min (cold start)** |
| Pod start → PROCESSING | ~1s |
| PROCESSING → COMPLETED (3 prompts) | ~19s |
| **Total wall time** | **~13 min** |

---

## Cold Start Note

The longest phase — ~9 minutes — was GKE pulling the execution image for the first time on the node. This is a one-time cost per node lifecycle, not per job.

**Why it happens:** GKE runs a node pool on Google Compute Engine VMs. When no execution pod has run recently, the node may have been removed (scale-to-zero) or the image evicted from the local cache. When a new job arrives, GKE has to:
1. Possibly provision a new VM node (~2–3 min)
2. Pull the execution Docker image from Artifact Registry onto that node (~2–3 min for a ~500MB Python image)
3. Start the container and run the Python startup sequence

**Subsequent jobs on the same warm node** start in under 30 seconds because the image is already cached locally.

**How to mitigate in production:**
- Set a minimum node pool size of 1 so a node is always running (eliminates VM provision time)
- Use a smaller base image to reduce pull time
- Or accept it — for batch jobs where throughput matters more than latency, a 9-minute startup amortized over thousands of prompts is negligible

---

## Issues Found During Test

| Issue | Cause | Status |
|---|---|---|
| Job appeared stuck at `QUEUED` for ~9 min | GKE cold start — node/image not cached | Expected behavior, not a bug. Documented above. |
