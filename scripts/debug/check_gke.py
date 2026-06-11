"""Get pod logs for crashing execution pod."""
import sys, os
sys.path.insert(0, r'c:\Users\Paramjeet\Desktop\JOB_90_DAYS\Projects\PromptForge-Distributed-LLM-Job-Processing-Platform')

import google.auth, google.auth.transport.requests
from kubernetes import client as k8s

credentials, _ = google.auth.default(
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
credentials.refresh(google.auth.transport.requests.Request())

configuration = k8s.Configuration()
configuration.host = "https://34.31.83.10"
configuration.api_key = {"authorization": f"Bearer {credentials.token}"}
configuration.verify_ssl = False

core_api = k8s.CoreV1Api(k8s.ApiClient(configuration))

# Get logs from the crashing pod (previous terminated container)
pod_name = "promptforge-exec-38f098da722b-wfjlc"
try:
    logs = core_api.read_namespaced_pod_log(
        name=pod_name,
        namespace='default',
        previous=True,   # get logs from last terminated container
        tail_lines=50
    )
    print("=== Previous container logs ===")
    print(logs)
except Exception as e:
    print("previous logs error:", e)

try:
    logs = core_api.read_namespaced_pod_log(
        name=pod_name,
        namespace='default',
        tail_lines=50
    )
    print("=== Current container logs ===")
    print(logs)
except Exception as e:
    print("current logs error:", e)
