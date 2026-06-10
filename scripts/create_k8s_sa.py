"""
Create the 'promptforge-exec' Kubernetes ServiceAccount with Workload Identity annotation.
This is a one-time setup step that the Pulumi infra didn't do.
"""
import warnings
warnings.filterwarnings("ignore")
import google.auth
import google.auth.transport.requests
from kubernetes import client as k8s

GCP_PROJECT = "promptforge-1212"
GCP_EXEC_SA = f"promptforge-exec@{GCP_PROJECT}.iam.gserviceaccount.com"

credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
credentials.refresh(google.auth.transport.requests.Request())

cfg = k8s.Configuration()
cfg.host = "https://34.31.83.10"
cfg.api_key = {"authorization": "Bearer " + credentials.token}
cfg.verify_ssl = False
api_client = k8s.ApiClient(cfg)
core = k8s.CoreV1Api(api_client)

sa = k8s.V1ServiceAccount(
    metadata=k8s.V1ObjectMeta(
        name="promptforge-exec",
        namespace="default",
        annotations={
            "iam.gke.io/gcp-service-account": GCP_EXEC_SA,
        },
    )
)

try:
    result = core.create_namespaced_service_account("default", sa)
    print(f"Created ServiceAccount: {result.metadata.name}")
    print(f"  annotation: iam.gke.io/gcp-service-account = {GCP_EXEC_SA}")
except k8s.exceptions.ApiException as e:
    if e.status == 409:
        print("ServiceAccount 'promptforge-exec' already exists — patching annotation")
        core.patch_namespaced_service_account(
            "promptforge-exec", "default",
            {"metadata": {"annotations": {"iam.gke.io/gcp-service-account": GCP_EXEC_SA}}}
        )
        print("  Patched.")
    else:
        raise

# Verify
sa_check = core.read_namespaced_service_account("promptforge-exec", "default")
print(f"\nVerified: {sa_check.metadata.name} in {sa_check.metadata.namespace}")
print(f"  annotations: {sa_check.metadata.annotations}")
