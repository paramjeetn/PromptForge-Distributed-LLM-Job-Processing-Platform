from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from google.cloud import firestore

from shared.models.job import JobRecord, JobStatus

_client: Optional[firestore.Client] = None


def _get_client() -> firestore.Client:
    global _client
    if _client is None:
        _client = firestore.Client(project=os.environ["FIRESTORE_PROJECT_ID"])
    return _client


def create_job(job: JobRecord) -> None:
    doc = _get_client().collection("jobs").document(job.job_id)
    doc.set(job.model_dump(mode="json"))


def get_job(job_id: str) -> Optional[JobRecord]:
    doc = _get_client().collection("jobs").document(job_id).get()
    if not doc.exists:
        return None
    return JobRecord(**doc.to_dict())


def update_job(job_id: str, **fields) -> None:
    _get_client().collection("jobs").document(job_id).update(fields)


def get_oldest_pending_job(client_id: str) -> Optional[JobRecord]:
    docs = (
        _get_client()
        .collection("jobs")
        .where("client_id", "==", client_id)
        .where("status", "==", JobStatus.PENDING)
        .order_by("created_at")
        .limit(1)
        .stream()
    )
    for doc in docs:
        return JobRecord(**doc.to_dict())
    return None
