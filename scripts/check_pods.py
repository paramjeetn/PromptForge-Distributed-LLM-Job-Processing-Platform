"""Query GKE directly to check pod status and logs."""
import base64
import tempfile
import google.auth
import google.auth.transport.requests
from kubernetes import client as k8s

# Get credentials
credentials, _ = google.auth.default(
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
credentials.refresh(google.auth.transport.requests.Request())

configuration = k8s.Configuration()
configuration.host = "https://34.31.83.10"
configuration.api_key = {"authorization": f"Bearer {credentials.token}"}
configuration.verify_ssl = False

api_client = k8s.ApiClient(configuration)
core_api = k8s.CoreV1Api(api_client)
batch_api = k8s.BatchV1Api(api_client)

# List jobs
print("=== Jobs ===")
jobs = batch_api.list_namespaced_job("default", label_selector="app=promptforge-exec")
for job in jobs.items:
    print(f"  {job.metadata.name}")
    if job.status.active:
        print(f"    active: {job.status.active}")
    if job.status.failed:
        print(f"    failed: {job.status.failed}")
    if job.status.succeeded:
        print(f"    succeeded: {job.status.succeeded}")
    if job.status.conditions:
        for c in job.status.conditions:
            print(f"    condition: {c.type} - {c.reason} - {c.message}")

# List pods
print("\n=== Pods ===")
pods = core_api.list_namespaced_pod("default", label_selector="app=promptforge-exec")
for pod in pods.items:
    print(f"  {pod.metadata.name}")
    print(f"    phase: {pod.status.phase}")
    for cs in (pod.status.container_statuses or []):
        print(f"    container {cs.name}: ready={cs.ready} restarts={cs.restart_count}")
        if cs.state.waiting:
            print(f"      waiting: {cs.state.waiting.reason} - {cs.state.waiting.message}")
        if cs.state.terminated:
            print(f"      terminated: exit={cs.state.terminated.exit_code} reason={cs.state.terminated.reason}")
            if cs.state.terminated.message:
                print(f"      message: {cs.state.terminated.message}")

    # Try to get logs
    if pod.status.phase in ("Running", "Succeeded", "Failed"):
        try:
            logs = core_api.read_namespaced_pod_log(
                pod.metadata.name, "default",
                container="execution", tail_lines=50
            )
            if logs:
                print(f"    --- logs ---")
                print(logs[:2000])
        except Exception as e:
            print(f"    (no logs: {e})")
