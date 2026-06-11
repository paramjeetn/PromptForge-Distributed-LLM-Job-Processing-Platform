import sys, os
sys.path.insert(0, r'c:\Users\Paramjeet\Desktop\JOB_90_DAYS\Projects\PromptForge-Distributed-LLM-Job-Processing-Platform')
os.environ['FIRESTORE_PROJECT_ID'] = 'promptforge-1212'
from shared.firestore import _get_client
db = _get_client()
jobs = list(db.collection('jobs').where('client_id', '==', 'e2e-test-client').stream())
jobs.sort(key=lambda j: j.to_dict().get('created_at') or '', reverse=True)
for j in jobs[:5]:
    d = j.to_dict()
    ca = d.get('created_at')
    print(j.id[:12], d.get('status'), str(ca)[:19] if ca else '')
