"""Check k8s service accounts and job details."""
import warnings
warnings.filterwarnings("ignore")
import google.auth
import google.auth.transport.requests
from kubernetes import client as k8s

credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
credentials.refresh(google.auth.transport.requests.Request())

cfg = k8s.Configuration()
cfg.host = "https://34.31.83.10"
cfg.api_key = {"authorization": "Bearer " + credentials.token}
cfg.verify_ssl = False
api_client = k8s.ApiClient(cfg)

core = k8s.CoreV1Api(api_client)
batch = k8s.BatchV1Api(api_client)

# Service accounts
print("Service accounts in default namespace:")
for sa in core.list_namespaced_service_account("default").items:
    print(f"  {sa.metadata.name}")
    if sa.metadata.annotations:
        for k, v in sa.metadata.annotations.items():
            if "iam" in k or "workload" in k:
                print(f"    {k}: {v}")

# Job status
print("\nJobs:")
jobs = batch.list_namespaced_job("default", label_selector="app=promptforge-exec")
for job in jobs.items:
    s = job.status
    print(f"  {job.metadata.name}")
    print(f"    active={s.active} failed={s.failed} succeeded={s.succeeded}")
    if s.conditions:
        for c in s.conditions:
            print(f"    condition: {c.type} {c.reason} {c.message}")

# All pods in default namespace (not just exec)
print("\nAll pods in default namespace:")
pods = core.list_namespaced_pod("default")
for pod in pods.items:
    cs_info = []
    for cs in (pod.status.container_statuses or []):
        state = "?"
        if cs.state.running:
            state = "running"
        elif cs.state.waiting:
            state = f"waiting:{cs.state.waiting.reason}"
        elif cs.state.terminated:
            state = f"terminated:{cs.state.terminated.exit_code}"
        cs_info.append(f"{cs.name}={state}(restarts={cs.restart_count})")
    print(f"  {pod.metadata.name} phase={pod.status.phase} {' '.join(cs_info)}")
