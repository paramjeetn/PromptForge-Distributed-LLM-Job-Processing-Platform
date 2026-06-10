"""GET /v1/jobs/{job_id}/status — Phase 9."""

from __future__ import annotations

from typing import Annotated, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from middleware.auth import verify_api_key
from shared.firestore import get_job
from shared.models.job import JobStatus

router = APIRouter()


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    provider: str
    model: str
    prompt_count: Optional[int]
    completed_count: Optional[int]
    failed_count: Optional[int]
    created_at: datetime
    started_processing_at: Optional[datetime]
    completed_at: Optional[datetime]


@router.get("/jobs/{job_id}/status", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    client_id: Annotated[str, Depends(verify_api_key)],
):
    job = get_job(job_id)
    if not job or job.client_id != client_id:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        provider=job.provider,
        model=job.model,
        prompt_count=job.prompt_count,
        completed_count=job.completed_count,
        failed_count=job.failed_count,
        created_at=job.created_at,
        started_processing_at=job.started_processing_at,
        completed_at=job.completed_at,
    )
