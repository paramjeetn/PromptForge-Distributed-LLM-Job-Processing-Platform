"""Delete all promptforge-exec GKE jobs."""
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
batch = k8s.BatchV1Api(k8s.ApiClient(cfg))

jobs = batch.list_namespaced_job("default", label_selector="app=promptforge-exec")
if not jobs.items:
    print("No GKE jobs to delete.")
else:
    for job in jobs.items:
        batch.delete_namespaced_job(
            job.metadata.name, "default",
            body=k8s.V1DeleteOptions(propagation_policy="Foreground")
        )
        print(f"Deleted: {job.metadata.name}")
