"""
Seed a job directly into Firestore + upload prompts.jsonl to GCS.
Simulates what POST /v1/jobs/init + PUT upload_url does,
bypassing Unkey auth for end-to-end testing.

Usage:
    python scripts/seed_job.py --prompts tests/prompts_10.jsonl \
        --provider gemini --model gemini-2.0-flash-lite \
        --api-key-ref projects/517402593902/secrets/gemini-api-key/versions/latest

Outputs: job_id and the GCS path where results will appear.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Allow importing shared/ from project root
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

import os
os.environ.setdefault("GCS_INPUT_BUCKET", "promptforge-input-promptforge-1212")
os.environ.setdefault("GCS_OUTPUT_BUCKET", "promptforge-output-promptforge-1212")
os.environ.setdefault("FIRESTORE_PROJECT_ID", "promptforge-1212")

from shared.firestore import create_job
from shared.gcs import write_bytes
from shared.models.job import JobRecord, JobStatus


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompts", required=True, help="Path to prompts.jsonl file")
    parser.add_argument("--provider", default="gemini")
    parser.add_argument("--model", default="gemini-2.0-flash-lite")
    parser.add_argument("--client-id", default="test-client")
    parser.add_argument("--rpm", type=int, default=15)
    parser.add_argument("--tpm", type=int, default=0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument(
        "--api-key-ref",
        default="projects/517402593902/secrets/gemini-api-key/versions/latest",
        help="Secret Manager resource name for the provider API key",
    )
    args = parser.parse_args()

    prompts_file = Path(args.prompts)
    if not prompts_file.exists():
        print(f"ERROR: {prompts_file} not found", file=sys.stderr)
        sys.exit(1)

    content = prompts_file.read_bytes()
    prompt_count = sum(1 for line in content.splitlines() if line.strip())

    job_id = str(uuid.uuid4())
    input_bucket = os.environ["GCS_INPUT_BUCKET"]
    upload_path = f"{args.client_id}/{job_id}/prompts.jsonl"

    # 1. Create Firestore job record FIRST (before upload triggers Eventarc)
    job = JobRecord(
        job_id=job_id,
        client_id=args.client_id,
        provider=args.provider,
        model=args.model,
        status=JobStatus.AWAITING_UPLOAD,
        max_retries=args.max_retries,
        rpm=args.rpm,
        tpm=args.tpm,
        upload_path=upload_path,
        api_key_ref=args.api_key_ref,
        created_at=datetime.now(timezone.utc),
    )
    create_job(job)
    print(f"Created Firestore job: {job_id}")

    # 2. Upload prompts to GCS (triggers Eventarc AFTER Firestore record exists)
    write_bytes(input_bucket, upload_path, content)
    print(f"Uploaded {prompt_count} prompts -> gs://{input_bucket}/{upload_path}")

    print()
    print("=== Job seeded ===")
    print(f"  job_id     : {job_id}")
    print(f"  client_id  : {args.client_id}")
    print(f"  provider   : {args.provider}/{args.model}")
    print(f"  rpm_limit  : {args.rpm}")
    print(f"  prompts    : {prompt_count}")
    print()
    print("Eventarc will fire when the file is noticed (~30s).")
    print(f"Watch results at: gs://promptforge-output-promptforge-1212/{args.client_id}/{job_id}/"  )
    print()
    print("Monitor GKE pod:")
    print("  kubectl get pods -l app=promptforge-exec")
    print("  kubectl logs -l app=promptforge-exec -f")


if __name__ == "__main__":
    main()
