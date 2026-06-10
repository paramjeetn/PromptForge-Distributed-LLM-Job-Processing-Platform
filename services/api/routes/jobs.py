from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from middleware.auth import verify_api_key
from shared.firestore import create_job
from shared.gcs import generate_signed_upload_url
from shared.models.job import JobRecord, JobStatus

router = APIRouter()

SUPPORTED_PROVIDERS = {"openai", "anthropic", "gemini", "mistral"}


class JobInitRequest(BaseModel):
    provider: str
    model: str
    max_retries: int = 3
    rpm: Optional[int] = None
    tpm: Optional[int] = None


class JobInitResponse(BaseModel):
    job_id: str
    upload_url: str
    expires_at: datetime


@router.post("/jobs/init", response_model=JobInitResponse, status_code=202)
async def job_init(
    body: JobInitRequest,
    client_id: Annotated[str, Depends(verify_api_key)],
):
    if body.provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported provider '{body.provider}'. Choose from: {sorted(SUPPORTED_PROVIDERS)}",
        )

    job_id = str(uuid.uuid4())
    input_bucket = os.environ["GCS_INPUT_BUCKET"]

    # GCS path: {client_id}/{job_id}/prompts.jsonl
    upload_path = f"{client_id}/{job_id}/prompts.jsonl"

    upload_url, expires_at = generate_signed_upload_url(
        bucket_name=input_bucket,
        blob_path=upload_path,
        expiry_minutes=15,
    )

    # Point the execution pod at the provider's API key in Secret Manager.
    # Convention: projects/{project}/secrets/{provider}-api-key/versions/latest
    gcp_project = os.environ.get("GCP_PROJECT_ID", "")
    api_key_ref = (
        f"projects/{gcp_project}/secrets/{body.provider}-api-key/versions/latest"
        if gcp_project else None
    )

    job = JobRecord(
        job_id=job_id,
        client_id=client_id,
        provider=body.provider,
        model=body.model,
        status=JobStatus.AWAITING_UPLOAD,
        max_retries=body.max_retries,
        rpm=body.rpm,
        tpm=body.tpm,
        api_key_ref=api_key_ref,
        upload_path=upload_path,
        created_at=datetime.now(timezone.utc),
    )

    create_job(job)

    return JobInitResponse(
        job_id=job_id,
        upload_url=upload_url,
        expires_at=expires_at,
    )
