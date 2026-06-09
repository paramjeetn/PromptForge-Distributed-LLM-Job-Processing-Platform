from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class JobStatus(str, Enum):
    AWAITING_UPLOAD = "AWAITING_UPLOAD"
    QUEUED = "QUEUED"
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class JobRecord(BaseModel):
    job_id: str
    client_id: str

    provider: str
    model: str
    status: JobStatus
    max_retries: int = 3
    rpm: Optional[int] = None
    tpm: Optional[int] = None

    # Secret Manager resource name for the provider API key, e.g.
    # "projects/my-project/secrets/openai-key/versions/latest"
    # Set when the job is created; injected into the execution pod as API_KEY_REF.
    api_key_ref: Optional[str] = None

    # Set after the client uploads prompts.jsonl
    upload_path: Optional[str] = None
    prompt_count: Optional[int] = None
    invalid_count: Optional[int] = None
    file_size_bytes: Optional[int] = None

    created_at: datetime
    uploaded_at: Optional[datetime] = None
    queued_at: Optional[datetime] = None
    started_processing_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
