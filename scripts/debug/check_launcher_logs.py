import subprocess, json

GCLOUD = r'C:\Users\Paramjeet\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd'

result = subprocess.run(
    [
        GCLOUD, 'logging', 'read',
        'resource.type=cloud_run_revision AND resource.labels.service_name=promptforge-launcher',
        '--project=promptforge-1212',
        '--limit=20',
        '--freshness=30m',
        '--format=json'
    ],
    capture_output=True, text=True, shell=True
)

logs = json.loads(result.stdout) if result.stdout.strip().startswith('[') else []
for l in logs:
    ts = l.get('timestamp', '')[:19]
    msg = l.get('textPayload', '') or str(l.get('jsonPayload', ''))
    print(ts, msg)
if result.stderr:
    print('STDERR:', result.stderr[:200])
