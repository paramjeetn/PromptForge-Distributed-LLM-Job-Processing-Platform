from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from middleware.auth import verify_api_key
from shared.firestore import create_job
from shared.gcs import generate_signed_upload_url
from shared.models.job import JobRecord, JobStatus

router = APIRouter()

# Supported models with their Tier 1 RPM/TPM limits.
# (provider, model) → (rpm, tpm)
# RPM/TPM are used as ceilings when the client doesn't specify them.
# The rate controller starts at 10 RPM and slow-starts up to these ceilings.
SUPPORTED_MODELS: dict[tuple[str, str], tuple[int, int]] = {
    # OpenAI
    ("openai",  "gpt-4o-mini"):       (500,  200_000),
    ("openai",  "gpt-4o"):            (500,   30_000),
    # Gemini
    ("gemini",  "gemini-2.0-flash"):  ( 15, 1_000_000),
    ("gemini",  "gemini-1.5-flash"):  ( 15, 1_000_000),
    ("gemini",  "gemini-1.5-pro"):    (  2,   32_000),
}


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
    if (body.provider, body.model) not in SUPPORTED_MODELS:
        supported = sorted(f"{p}/{m}" for p, m in SUPPORTED_MODELS)
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported provider/model '{body.provider}/{body.model}'. "
                   f"Supported: {supported}",
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

    # Fill in Tier 1 defaults if the client didn't specify limits
    default_rpm, default_tpm = SUPPORTED_MODELS[(body.provider, body.model)]
    rpm = body.rpm if body.rpm is not None else default_rpm
    tpm = body.tpm if body.tpm is not None else default_tpm

    job = JobRecord(
        job_id=job_id,
        client_id=client_id,
        provider=body.provider,
        model=body.model,
        status=JobStatus.AWAITING_UPLOAD,
        max_retries=body.max_retries,
        rpm=rpm,
        tpm=tpm,
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
