"""Stream pod logs in real-time."""
import warnings
warnings.filterwarnings("ignore")
import sys
import google.auth
import google.auth.transport.requests
from kubernetes import client as k8s
from kubernetes.stream import stream

credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
credentials.refresh(google.auth.transport.requests.Request())
cfg = k8s.Configuration()
cfg.host = "https://34.31.83.10"
cfg.api_key = {"authorization": "Bearer " + credentials.token}
cfg.verify_ssl = False
api_client = k8s.ApiClient(cfg)
core = k8s.CoreV1Api(api_client)

# Find the pod
pods = core.list_namespaced_pod("default", label_selector="app=promptforge-exec")
if not pods.items:
    print("No execution pods found.")
    sys.exit(0)

pod = pods.items[0]
pod_name = pod.metadata.name
print(f"Pod: {pod_name}  phase={pod.status.phase}")
print("--- logs (last 200 lines) ---")

try:
    logs = core.read_namespaced_pod_log(
        pod_name, "default",
        container="execution",
        tail_lines=200,
        timestamps=True,
        _preload_content=True,
    )
    if logs:
        print(logs)
    else:
        print("(no logs yet)")
except Exception as e:
    print(f"Error reading logs: {e}")

# Also check previous terminated container if any
print("\n--- previous container logs ---")
try:
    prev = core.read_namespaced_pod_log(
        pod_name, "default",
        container="execution",
        previous=True,
        tail_lines=50,
    )
    if prev:
        print(prev)
    else:
        print("(no previous container)")
except Exception:
    print("(no previous container)")
