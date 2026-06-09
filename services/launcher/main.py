"""
Cloud Run — Job Launcher

Receives OBJECT_FINALIZE CloudEvents from Eventarc when a client uploads
prompts.jsonl to the GCS input bucket.

Flow:
  1. Parse CloudEvent  → extract client_id, job_id, file_size
  2. Fetch job from Firestore
  3. Stream-validate prompts.jsonl line by line
  4. If client already has an active job → mark this one PENDING, exit
  5. Otherwise → mark QUEUED, create GKE Job via spawner
"""

from __future__ import annotations

import logging
import os

import sentry_sdk
from cloudevents.http import from_http
from datetime import datetime, timezone
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

load_dotenv()

sentry_sdk.init(
    dsn=os.getenv("SENTRY_DSN", ""),
    traces_sample_rate=0.1,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("launcher")

app = FastAPI(title="PromptForge Launcher", version="1.0.0")

from shared.firestore import get_job, has_active_job, update_job
from shared.models.job import JobStatus
from spawner import spawn_execution_job
from validator import InvalidFileError, validate_prompts


@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.post("/")
async def handle_upload(request: Request):
    # -----------------------------------------------------------------------
    # 1. Parse the CloudEvent
    # -----------------------------------------------------------------------
    body = await request.body()
    try:
        event = from_http(dict(request.headers), body)
    except Exception as exc:
        logger.error("Failed to parse CloudEvent: %s", exc)
        raise HTTPException(status_code=400, detail=f"Invalid CloudEvent: {exc}")

    data = event.data or {}
    bucket = data.get("bucket", "")
    object_name = data.get("name", "")       # "{client_id}/{job_id}/prompts.jsonl"
    file_size = int(data.get("size", 0))

    # Only process our expected path pattern; silently ignore anything else.
    parts = object_name.split("/")
    if len(parts) != 3 or parts[2] != "prompts.jsonl":
        logger.info("Ignoring unexpected object: %s", object_name)
        return JSONResponse({"status": "ignored"})

    client_id, job_id = parts[0], parts[1]
    output_prefix = f"{client_id}/{job_id}"

    logger.info("Received upload event: job_id=%s client_id=%s size=%d", job_id, client_id, file_size)

    # -----------------------------------------------------------------------
    # 2. Fetch job metadata from Firestore
    # -----------------------------------------------------------------------
    job = get_job(job_id)
    if job is None:
        logger.warning("No Firestore record for job_id=%s — ignoring", job_id)
        return JSONResponse({"status": "not_found"})

    if job.status != JobStatus.AWAITING_UPLOAD:
        # Eventarc delivered the event twice; already processed.
        logger.info("job_id=%s already in status=%s — ignoring duplicate event", job_id, job.status)
        return JSONResponse({"status": "duplicate"})

    # -----------------------------------------------------------------------
    # 3. Stream-validate prompts.jsonl
    # -----------------------------------------------------------------------
    input_bucket = os.environ["GCS_INPUT_BUCKET"]
    output_bucket = os.environ["GCS_OUTPUT_BUCKET"]

    try:
        result = validate_prompts(
            input_bucket=input_bucket,
            blob_path=object_name,
            output_bucket=output_bucket,
            output_prefix=output_prefix,
        )
    except InvalidFileError as exc:
        logger.error("job_id=%s validation failed: %s", job_id, exc)
        update_job(job_id, status=JobStatus.FAILED)
        return JSONResponse({"status": "failed", "reason": str(exc)})

    logger.info(
        "job_id=%s validation done: prompt_count=%d invalid_count=%d",
        job_id, result.prompt_count, result.invalid_count,
    )

    # -----------------------------------------------------------------------
    # 4. Check for an active job for this client
    # -----------------------------------------------------------------------
    if has_active_job(client_id):
        logger.info("job_id=%s client_id=%s already has active job — marking PENDING", job_id, client_id)
        update_job(
            job_id,
            status=JobStatus.PENDING,
            uploaded_at=datetime.now(timezone.utc),
            file_size_bytes=file_size,
            prompt_count=result.prompt_count,
            invalid_count=result.invalid_count,
        )
        return JSONResponse({"status": "pending"})

    # -----------------------------------------------------------------------
    # 5. Mark QUEUED and spawn the execution pod
    # -----------------------------------------------------------------------
    now = datetime.now(timezone.utc)
    update_job(
        job_id,
        status=JobStatus.QUEUED,
        uploaded_at=now,
        queued_at=now,
        file_size_bytes=file_size,
        prompt_count=result.prompt_count,
        invalid_count=result.invalid_count,
    )

    spawn_execution_job(
        job=job,
        prompt_count=result.prompt_count,
        prompts_path=object_name,
    )

    logger.info("job_id=%s GKE Job created, status=QUEUED", job_id)
    return JSONResponse({"status": "queued"})
