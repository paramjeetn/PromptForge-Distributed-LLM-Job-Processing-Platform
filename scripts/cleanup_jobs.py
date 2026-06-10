"""List and clean up test jobs in Firestore."""
import sys
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("FIRESTORE_PROJECT_ID", "promptforge-1212")

from shared.firestore import _get_client, update_job
from shared.models.job import JobStatus

client = _get_client()
docs = list(client.collection("jobs").where("client_id", "==", "test-client").stream())

if not docs:
    print("No jobs found for test-client")
    sys.exit(0)

print(f"Found {len(docs)} jobs:")
for doc in docs:
    d = doc.to_dict()
    print(f"  {d['job_id']} status={d['status']}")

if len(sys.argv) > 1 and sys.argv[1] == "--cleanup":
    for doc in docs:
        d = doc.to_dict()
        if d["status"] in ("QUEUED", "PENDING", "AWAITING_UPLOAD"):
            update_job(d["job_id"], status=JobStatus.FAILED)
            print(f"  Marked {d['job_id'][:8]} as FAILED")
    print("Cleanup done.")
else:
    print("\nRun with --cleanup to mark stale jobs as FAILED")
