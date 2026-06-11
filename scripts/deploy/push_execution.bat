@echo off
cd /d "c:\Users\Paramjeet\Desktop\JOB_90_DAYS\Projects\PromptForge-Distributed-LLM-Job-Processing-Platform"
call gcloud auth configure-docker us-central1-docker.pkg.dev --quiet
docker buildx build --platform linux/amd64 --push -t us-central1-docker.pkg.dev/promptforge-1212/promptforge/execution:latest -f services/execution/Dockerfile .
