"""
Execution pod entrypoint.

Reads env vars injected by the launcher, fetches the provider API key,
drives the dispatch loop, and updates Firestore on completion or failure.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

# Ensure shared/ and services/execution/ are importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dotenv import load_dotenv

load_dotenv()

from shared.observability import setup as setup_observability
setup_observability("execution")

from shared.firestore import update_job
from shared.models.job import JobStatus
from shared.secrets import fetch_api_key
from services.execution.config import from_env
from services.execution.loops.dispatch import run
from services.execution.queue import maybe_start_next_job


async def _main() -> None:
    config = from_env()

    # ── Mark job as PROCESSING ────────────────────────────────────────
    update_job(
        config.job_id,
        status=JobStatus.PROCESSING,
        started_processing_at=datetime.now(timezone.utc),
    )
    print(f"[execution] job={config.job_id} status=PROCESSING provider={config.provider}/{config.model}", flush=True)

    # ── Fetch API key ─────────────────────────────────────────────────
    print(f"[execution] fetching api_key_ref={config.api_key_ref!r}", flush=True)
    api_key = fetch_api_key(config.api_key_ref)
    print(f"[execution] api_key fetched (len={len(api_key)})", flush=True)

    # ── Run dispatch loop ─────────────────────────────────────────────
    try:
        result = await run(config, api_key)
    except Exception as exc:
        print(f"[execution] FATAL job={config.job_id} error={exc}", file=sys.stderr)
        update_job(config.job_id, status=JobStatus.FAILED)
        sys.exit(1)

    # ── Mark job as COMPLETED ─────────────────────────────────────────
    update_job(
        config.job_id,
        status=JobStatus.COMPLETED,
        completed_at=datetime.now(timezone.utc),
        completed_count=result.completed,
        failed_count=result.failed,
    )
    print(
        f"[execution] job={config.job_id} status=COMPLETED "
        f"completed={result.completed} failed={result.failed} "
        f"parts={len(result.parts_written)}",
        flush=True,
    )

    # ── Phase 8: Start next queued job for this client ────────────────
    maybe_start_next_job(config)


if __name__ == "__main__":
    asyncio.run(_main())
