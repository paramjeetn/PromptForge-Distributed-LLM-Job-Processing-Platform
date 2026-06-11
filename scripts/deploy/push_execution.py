import subprocess, sys, os

os.chdir(r'c:\Users\Paramjeet\Desktop\JOB_90_DAYS\Projects\PromptForge-Distributed-LLM-Job-Processing-Platform')

# Step 1: configure docker auth
r = subprocess.run(
    'gcloud auth configure-docker us-central1-docker.pkg.dev --quiet',
    shell=True, capture_output=False
)
print('gcloud configure exit:', r.returncode)

# Step 2: build and push
r = subprocess.run(
    'docker buildx build --platform linux/amd64 --push '
    '-t us-central1-docker.pkg.dev/promptforge-1212/promptforge/execution:latest '
    '-f services/execution/Dockerfile .',
    shell=True, capture_output=False
)
print('docker build exit:', r.returncode)
sys.exit(r.returncode)
