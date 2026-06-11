"""
End-to-end test — real user flow against the live deployed system.

Flow:
  1. POST /v1/jobs/init  → get job_id + signed upload URL
  2. PUT prompts.jsonl   → upload directly to GCS via signed URL
  3. Poll GET /status    → wait for COMPLETED (Eventarc → Launcher → GKE → Execution)
  4. GET /results        → get signed download URLs
  5. Download each file  → verify every prompt has a response

Prerequisites:
  - API deployed to Cloud Run
  - Eventarc trigger active (GCS → Launcher)
  - GKE cluster running with promptforge-exec service account
  - Execution image pushed to Artifact Registry

Run:
    pytest tests/e2e/test_e2e.py -v -s
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx
import pytest

# ── Config ────────────────────────────────────────────────────────────────────

API_BASE_URL = os.environ.get("E2E_API_URL", "https://promptforge-api-517402593902.us-central1.run.app")
API_KEY      = os.environ["E2E_API_KEY"]

# How long to wait for a job to complete (Eventarc + GKE cold start can take ~3-4 min)
POLL_TIMEOUT_SECONDS  = 600   # 10 minutes
POLL_INTERVAL_SECONDS = 15

HEADERS = {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json",
}

# ── Prompt fixture ─────────────────────────────────────────────────────────────

def _make_prompts_jsonl(n: int = 5) -> bytes:
    lines = [
        json.dumps({"prompt_id": i, "prompt": f"What is {i} + 1? Reply with just the number."})
        for i in range(1, n + 1)
    ]
    return "\n".join(lines).encode()


# ── Test ──────────────────────────────────────────────────────────────────────

class TestE2EFullFlow:

    def test_submit_poll_results(self):
        """
        Full client flow: init → upload → wait → status → results → verify.
        """
        client = httpx.Client(timeout=30)

        # ── Step 1: POST /v1/jobs/init ─────────────────────────────────────
        print("\n[e2e] Step 1: POST /v1/jobs/init")
        init_resp = client.post(
            f"{API_BASE_URL}/v1/jobs/init",
            headers=HEADERS,
            json={
                "provider":    "openai",
                "model":       "gpt-4o-mini",
                "rpm":         60,
                "tpm":         10000,
                "max_retries": 3,
            },
        )
        assert init_resp.status_code == 202, f"init failed: {init_resp.status_code} {init_resp.text}"

        body = init_resp.json()
        job_id     = body["job_id"]
        upload_url = body["upload_url"]
        expires_at = body["expires_at"]

        print(f"[e2e] job_id={job_id}")
        print(f"[e2e] upload_url expires_at={expires_at}")

        assert job_id
        assert upload_url.startswith("https://storage.googleapis.com/")

        # ── Step 2: PUT prompts.jsonl to signed GCS URL ────────────────────
        print(f"[e2e] Step 2: uploading prompts.jsonl to GCS")
        prompts = _make_prompts_jsonl(5)

        upload_resp = httpx.put(
            upload_url,
            content=prompts,
            headers={"Content-Type": "application/x-ndjson"},
            timeout=30,
        )
        assert upload_resp.status_code == 200, (
            f"GCS upload failed: {upload_resp.status_code} {upload_resp.text}"
        )
        print(f"[e2e] upload OK ({len(prompts)} bytes)")

        # ── Step 3: Poll GET /status until COMPLETED ───────────────────────
        print(f"[e2e] Step 3: polling status (timeout={POLL_TIMEOUT_SECONDS}s)")
        deadline = time.time() + POLL_TIMEOUT_SECONDS
        final_status = None

        while time.time() < deadline:
            status_resp = client.get(
                f"{API_BASE_URL}/v1/jobs/{job_id}/status",
                headers=HEADERS,
            )
            assert status_resp.status_code == 200, (
                f"status check failed: {status_resp.status_code} {status_resp.text}"
            )

            status_body = status_resp.json()
            current     = status_body["status"]
            print(f"[e2e]   status={current}")

            if current == "COMPLETED":
                final_status = status_body
                break

            if current == "FAILED":
                pytest.fail(f"Job {job_id} reached FAILED status")

            time.sleep(POLL_INTERVAL_SECONDS)

        assert final_status is not None, (
            f"Job {job_id} did not complete within {POLL_TIMEOUT_SECONDS}s. Last status={current}"
        )

        print(f"[e2e] COMPLETED: completed={final_status['completed_count']} failed={final_status['failed_count']}")

        assert final_status["prompt_count"]    == 5
        assert final_status["completed_count"] == 5
        assert final_status["failed_count"]    == 0

        # ── Step 4: GET /results → signed download URLs ────────────────────
        print(f"[e2e] Step 4: GET /v1/jobs/{job_id}/results")
        results_resp = client.get(
            f"{API_BASE_URL}/v1/jobs/{job_id}/results",
            headers=HEADERS,
        )
        assert results_resp.status_code == 200, (
            f"results failed: {results_resp.status_code} {results_resp.text}"
        )

        results_body = results_resp.json()
        assert results_body["status"] == "COMPLETED"
        files = results_body["files"]
        assert len(files) >= 1, "Expected at least one result file"

        print(f"[e2e] {len(files)} result file(s) returned")
        for f in files:
            print(f"[e2e]   {f['filename']}  expires={f['expires_at']}")
            assert f["url"].startswith("https://storage.googleapis.com/")
            assert "results_" in f["filename"]

        # ── Step 5: Download files and verify content ──────────────────────
        print(f"[e2e] Step 5: downloading and verifying result files")
        all_records = []

        for f in files:
            dl_resp = httpx.get(f["url"], timeout=30)
            assert dl_resp.status_code == 200, (
                f"download failed for {f['filename']}: {dl_resp.status_code}"
            )
            for line in dl_resp.text.strip().splitlines():
                if line.strip():
                    all_records.append(json.loads(line))

        print(f"[e2e] downloaded {len(all_records)} result records")

        # Every prompt must have a response
        assert len(all_records) == 5, f"Expected 5 results, got {len(all_records)}"

        prompt_ids = {r["prompt_id"] for r in all_records}
        assert prompt_ids == {1, 2, 3, 4, 5}, f"Missing prompt IDs: {prompt_ids}"

        for r in all_records:
            assert "response" in r and r["response"], f"Empty response for prompt_id={r['prompt_id']}"
            assert "tokens"     in r
            assert "latency_ms" in r
            print(f"[e2e]   prompt_id={r['prompt_id']} response={r['response']!r} tokens={r['tokens']}")

        print(f"[e2e] ALL CHECKS PASSED")
