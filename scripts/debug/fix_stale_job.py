import sys
import os

sys.path.insert(0, r'c:\Users\Paramjeet\Desktop\JOB_90_DAYS\Projects\PromptForge-Distributed-LLM-Job-Processing-Platform')
os.environ['FIRESTORE_PROJECT_ID'] = 'promptforge-1212'

from shared.firestore import _get_client
from shared.models.job import JobStatus

db = _get_client()

# Find all non-terminal jobs for e2e-test-client
active_statuses = [
    JobStatus.AWAITING_UPLOAD.value,
    JobStatus.QUEUED.value,
    JobStatus.PROCESSING.value,
    JobStatus.PENDING.value,
]

jobs = db.collection('jobs').where('client_id', '==', 'e2e-test-client').stream()
cleared = 0
for job in jobs:
    data = job.to_dict()
    status = data.get('status')
    if status in active_statuses:
        job.reference.update({'status': JobStatus.FAILED.value})
        print(f"marked {job.id} ({status}) -> FAILED")
        cleared += 1

print(f"done — cleared {cleared} stale jobs")
