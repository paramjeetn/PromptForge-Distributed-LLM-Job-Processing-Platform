"""Check GCS output bucket for results and print them."""
import os
import sys
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from google.cloud import storage

gcs = storage.Client()

for bucket_name in ["promptforge-output-promptforge-1212", "promptforge-input-promptforge-1212"]:
    bucket = gcs.bucket(bucket_name)
    blobs = list(bucket.list_blobs())
    label = "OUTPUT" if "output" in bucket_name else "INPUT"
    if blobs:
        print(f"\n{label} bucket ({len(blobs)} files):")
        for blob in blobs:
            print(f"  {blob.name}  ({blob.size} bytes)")
            if blob.name.endswith(".jsonl") and blob.size and blob.size < 50000:
                data = blob.download_as_bytes().decode()
                for line in data.strip().splitlines()[:5]:
                    try:
                        rec = json.loads(line)
                        if "response" in rec:
                            print(f"    prompt_id={rec['prompt_id']} response={rec['response'][:60]!r}")
                        elif "error" in rec:
                            print(f"    ERROR prompt_id={rec.get('prompt_id')} error={rec['error']}")
                    except Exception:
                        print(f"    raw: {line[:80]}")
    else:
        print(f"\n{label} bucket: empty")
