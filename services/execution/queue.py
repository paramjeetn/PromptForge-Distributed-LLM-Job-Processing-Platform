"""
Job queue — Phase 8.

After an execution pod finishes its job (COMPLETED), call maybe_start_next_job()
to pick up the oldest PENDING job for the same client and spawn a new pod for it.

This keeps the per-client pipeline flowing without any external orchestrator.
"""

from __future__ import annotations

import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.firestore import get_oldest_pending_job, update_job
from shared.gke import spawn_execution_job
from shared.models.job import JobStatus
from services.execution.config import JobConfig


def maybe_start_next_job(config: JobConfig) -> bool:
    """
    Find the oldest PENDING job for this client and promote it to QUEUED + spawn a pod.
    Returns True if a job was started, False if no pending jobs exist.

    Requires GKE_CLUSTER_ENDPOINT and EXECUTION_IMAGE to be set in the environment
    (passed down from the launcher when it spawned this pod).
    """
    if not os.environ.get("GKE_CLUSTER_ENDPOINT") or not os.environ.get("EXECUTION_IMAGE"):
        print("[queue] GKE_CLUSTER_ENDPOINT or EXECUTION_IMAGE not set — skipping queue check", flush=True)
        return False

    next_job = get_oldest_pending_job(config.client_id)
    if not next_job:
        print(f"[queue] no pending jobs for client={config.client_id}", flush=True)
        return False

    if not next_job.upload_path:
        print(f"[queue] pending job={next_job.job_id} has no upload_path — skipping", flush=True)
        return False

    update_job(
        next_job.job_id,
        status=JobStatus.QUEUED,
        queued_at=datetime.now(timezone.utc),
    )

    spawn_execution_job(
        job=next_job,
        prompt_count=next_job.prompt_count or 0,
        prompts_path=next_job.upload_path,
    )

    print(f"[queue] started next job={next_job.job_id} for client={config.client_id}", flush=True)
    return True
