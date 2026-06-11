import subprocess, sys

GCLOUD = r'C:\Users\Paramjeet\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd'

r = subprocess.run(
    [GCLOUD, 'builds', 'submit',
     '--config=cloudbuild-execution.yaml',
     '--project=promptforge-1212',
     '.'],
    cwd=r'c:\Users\Paramjeet\Desktop\JOB_90_DAYS\Projects\PromptForge-Distributed-LLM-Job-Processing-Platform',
    shell=True
)
print('exit:', r.returncode)
sys.exit(r.returncode)
