"""
Delete ALL test data: Firestore jobs + GCS blobs in input/output buckets.
Run this to get a clean slate before a fresh end-to-end test.
"""
import sys
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("FIRESTORE_PROJECT_ID", "promptforge-1212")
os.environ.setdefault("GCS_INPUT_BUCKET", "promptforge-input-promptforge-1212")
os.environ.setdefault("GCS_OUTPUT_BUCKET", "promptforge-output-promptforge-1212")

from google.cloud import firestore, storage

# ── Firestore ────────────────────────────────────────────────────────────────
fs = firestore.Client(project="promptforge-1212")
jobs = list(fs.collection("jobs").stream())
print(f"Deleting {len(jobs)} Firestore job(s)...")
for doc in jobs:
    d = doc.to_dict()
    print(f"  {d.get('job_id','?')[:8]} {d.get('client_id','?')} {d.get('status','?')}")
    doc.reference.delete()
print("Firestore clean.")

# ── GCS input bucket ─────────────────────────────────────────────────────────
gcs = storage.Client()
for bucket_name in ["promptforge-input-promptforge-1212", "promptforge-output-promptforge-1212"]:
    bucket = gcs.bucket(bucket_name)
    blobs = list(bucket.list_blobs())
    if blobs:
        print(f"\nDeleting {len(blobs)} blob(s) from {bucket_name}...")
        for blob in blobs:
            print(f"  {blob.name}")
            blob.delete()
    else:
        print(f"\n{bucket_name}: already empty")

print("\nAll clean. Ready for a fresh run.")
