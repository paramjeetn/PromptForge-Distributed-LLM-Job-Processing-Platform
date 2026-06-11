# PromptForge — Status Report
_Last updated: 2026-06-11_

---

## What's Working

### Infrastructure (GCP)
- GCS buckets: `promptforge-input`, `promptforge-output` — live and accessible
- GKE cluster — running, workload identity configured
- Firestore `jobs` collection — reads/writes confirmed working
- Eventarc `OBJECT_FINALIZE` trigger on input bucket -> Launcher — wired up
- Artifact Registry — images pushed and pullable
- Secret Manager — API keys stored and accessible from Cloud Run
- IAM / service accounts — least-privilege, no permission errors in API or Launcher

### API Service (Cloud Run)
- **Deployed:** `https://promptforge-api-517402593902.us-central1.run.app`
- `POST /v1/jobs/init` — creates Firestore job, returns signed GCS upload URL
- `GET /v1/jobs/{job_id}` — returns status + progress
- `GET /v1/jobs/{job_id}/results` — returns signed URLs for result files
- `DELETE /v1/jobs/{job_id}` — cancellation
- Auth: Secret Manager API key store working (test client `e2e-test-client`, key in Secret Manager)
- Correct JSON stored in `promptforge-api-keys` secret (version 3)

### Launcher Service (Cloud Run)
- Deployed and receives Eventarc `OBJECT_FINALIZE` events
- Validates `prompts.jsonl`, writes `errors.jsonl` for invalid lines
- Creates GKE Job with correct env vars
- Per-client concurrency enforced (one active pod, second job -> PENDING)

### Execution Service — Code
- Dispatch loop (LiteLLM -> OpenAI/Gemini) — implemented
- Result buffering (flush on 500 responses / 50 MB / 30s) — implemented
- Rate learning (slow-start, 429 backoff, P95 token window) — implemented
- Checkpointing (state.json every 30s, SIGTERM drain) — implemented
- Job completion + next-job promotion (`job_queue.py`) — implemented

### Observability
- Sentry wired up in all 3 services
- OTel/OTLP -> Axiom in all 3 services

### Bugs Fixed
| Bug | Root Cause | Fix |
|-----|-----------|-----|
| 401 "Invalid API key" | `.dockerignore` missing -> stale `auth.cpython-312.pyc` (old Unkey code) baked into image | Created `.dockerignore` excluding `**/__pycache__`, rebuilt API image |
| 500 "JSON parse error" on auth | `promptforge-api-keys` secret had malformed JSON (Windows `echo` ate quotes) | Recreated via Python `json.dump`, now version 3 |
| Execution pod CrashLoopBackOff | `queue.py` at `/app/services/execution/queue.py` — Python puts that dir on `sys.path[0]`, so `import queue` by urllib3 found the local file, causing circular import | Deleted `queue.py` — logic already in `job_queue.py`, stdlib `queue` now resolves correctly |
| Stale QUEUED jobs blocking submissions | Crashed pods left jobs in non-terminal states | `fix_stale_job.py` marks all active-state jobs for client as FAILED |
| `docker-credential-gcloud` not in PATH | Docker Desktop could not find gcloud credential helper | Used `gcloud auth print-access-token | docker login` for manual auth |

---

## What's Failing / Not Yet Verified

### E2E Test — Never Passed
- `tests/e2e/test_e2e.py` has never completed successfully against live GCP.
- Steps 1-2 (init + GCS upload) work fine.
- The execution pod was crashing (CrashLoopBackOff, 7 restarts) — root cause was the `queue.py` circular import.
- **Fix applied:** `queue.py` deleted, image rebuilt and pushed (manifest `bcd6b04b...`).
- **Next action:** Run `pytest tests/e2e/test_e2e.py -v -s` to verify.

### Unknown Until E2E Runs
- GKE pulls the updated image (`:latest` with `imagePullPolicy: Always`)
- All execution pod env vars are injected correctly
- LiteLLM can reach OpenAI and complete requests
- Result files are written to GCS and accessible via signed URLs
- Completion logic triggers correctly

---

## Next Steps (in order)

1. **Run E2E test:**
   ```
   pytest tests/e2e/test_e2e.py -v -s
   ```
   Timeout is 10 min — execution pod cold start + LLM calls take ~3-4 min.

2. If E2E fails, diagnose with:
   - Pod logs: `python check_gke.py`
   - Launcher logs: `gcloud run services logs read promptforge-launcher --region us-central1 --limit 50`
   - Clear stale jobs: `python fix_stale_job.py`

3. Once E2E passes — clean up temp/diagnostic files:
   `fix_stale_job.py`, `check_gke.py`, `check_jobs.py`, `check_secret.py`,
   `push_execution.py`, `push_execution.bat`, `submit_build.py`, `cloudbuild-execution.yaml`

4. Commit and tag `v1.0`

---

## Quick Reference

| Item | Value |
|------|-------|
| Live API URL | `https://promptforge-api-517402593902.us-central1.run.app` |
| Test API key | stored in Secret Manager `promptforge-api-keys` |
| Test client ID | `e2e-test-client` |
| GKE cluster | `promptforge-cluster` (us-central1) |
| Execution image | `us-central1-docker.pkg.dev/promptforge-1212/promptforge/execution:latest` |
| GCP project | `promptforge-1212` |
