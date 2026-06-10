"""GET /v1/jobs/{job_id}/results — Phase 9."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from middleware.auth import verify_api_key
from shared.firestore import get_job
from shared.gcs import list_result_files, generate_signed_download_url
from shared.models.job import JobStatus

router = APIRouter()

RESULTS_URL_TTL_MINUTES = 60


class ResultFile(BaseModel):
    filename: str
    url: str
    expires_at: datetime


class JobResultsResponse(BaseModel):
    job_id: str
    status: JobStatus
    files: list[ResultFile]


@router.get("/jobs/{job_id}/results", response_model=JobResultsResponse)
async def get_job_results(
    job_id: str,
    client_id: Annotated[str, Depends(verify_api_key)],
):
    job = get_job(job_id)
    if not job or job.client_id != client_id:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status not in (JobStatus.COMPLETED,):
        raise HTTPException(
            status_code=409,
            detail=f"Results not available — job is {job.status}",
        )

    output_bucket = os.environ["GCS_OUTPUT_BUCKET"]
    prefix = f"{client_id}/{job_id}/"
    paths = list_result_files(output_bucket, prefix)

    files = []
    for path in paths:
        url, expires_at = generate_signed_download_url(
            output_bucket, path, expiry_minutes=RESULTS_URL_TTL_MINUTES
        )
        files.append(ResultFile(
            filename=path.split("/")[-1],
            url=url,
            expires_at=expires_at,
        ))

    return JobResultsResponse(job_id=job_id, status=job.status, files=files)
