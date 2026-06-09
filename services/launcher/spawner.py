from __future__ import annotations

import base64
import os
import tempfile

import google.auth
import google.auth.transport.requests
from kubernetes import client as k8s

from shared.models.job import JobRecord


def spawn_execution_job(job: JobRecord, prompt_count: int, prompts_path: str) -> None:
    """
    Create a Kubernetes batch/v1 Job that runs the execution pod for the given job.

    The pod runs with restartPolicy=OnFailure so Kubernetes restarts it on crash.
    The launcher injects all required env vars; the pod reads them on startup.

    Idempotent: if a Job with the same name already exists (409 Conflict from the
    API server), the error is silently swallowed — the pod is already running.
    """
    batch_api = _get_batch_api()
    namespace = os.environ.get("GKE_NAMESPACE", "default")
    job_manifest = _build_job_manifest(job, prompt_count, prompts_path, namespace)

    try:
        batch_api.create_namespaced_job(namespace=namespace, body=job_manifest)
    except k8s.exceptions.ApiException as exc:
        if exc.status == 409:
            # Job already exists — Eventarc retry delivered the event twice.
            return
        raise


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_batch_api() -> k8s.BatchV1Api:
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(google.auth.transport.requests.Request())

    configuration = k8s.Configuration()
    configuration.host = f"https://{os.environ['GKE_CLUSTER_ENDPOINT']}"
    configuration.api_key = {"authorization": f"Bearer {credentials.token}"}

    ca_b64 = os.environ.get("GKE_CLUSTER_CA")
    if ca_b64:
        ca_data = base64.b64decode(ca_b64)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".crt")
        tmp.write(ca_data)
        tmp.flush()
        configuration.ssl_ca_cert = tmp.name
    else:
        # Acceptable for dev; set GKE_CLUSTER_CA in production.
        configuration.verify_ssl = False

    return k8s.BatchV1Api(k8s.ApiClient(configuration))


def _build_job_manifest(
    job: JobRecord,
    prompt_count: int,
    prompts_path: str,
    namespace: str,
) -> k8s.V1Job:
    env_vars = [
        k8s.V1EnvVar(name="JOB_ID",              value=job.job_id),
        k8s.V1EnvVar(name="PROVIDER",             value=job.provider),
        k8s.V1EnvVar(name="MODEL",                value=job.model),
        k8s.V1EnvVar(name="RPM_LIMIT",            value=str(job.rpm or 60)),
        k8s.V1EnvVar(name="TPM_LIMIT",            value=str(job.tpm or 0)),
        k8s.V1EnvVar(name="MAX_RETRIES",          value=str(job.max_retries)),
        k8s.V1EnvVar(name="INPUT_BUCKET",         value=os.environ["GCS_INPUT_BUCKET"]),
        k8s.V1EnvVar(name="OUTPUT_BUCKET",        value=os.environ["GCS_OUTPUT_BUCKET"]),
        k8s.V1EnvVar(name="PROMPTS_PATH",         value=prompts_path),
        k8s.V1EnvVar(name="PROMPT_COUNT",         value=str(prompt_count)),
        k8s.V1EnvVar(name="FIRESTORE_PROJECT_ID", value=os.environ["FIRESTORE_PROJECT_ID"]),
    ]
    if job.api_key_ref:
        env_vars.append(k8s.V1EnvVar(name="API_KEY_REF", value=job.api_key_ref))

    # K8s job name: "promptforge-exec-{first-8-chars-of-uuid}"
    # Full UUID would be valid too (53 chars total) but shorter is nicer in kubectl output.
    k8s_job_name = f"promptforge-exec-{job.job_id.replace('-', '')[:12]}"

    return k8s.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=k8s.V1ObjectMeta(
            name=k8s_job_name,
            namespace=namespace,
            labels={"app": "promptforge-exec", "promptforge-job-id": job.job_id},
        ),
        spec=k8s.V1JobSpec(
            # Allow up to 10 pod restarts before the Job is marked as Failed.
            backoff_limit=10,
            # Auto-delete the Job 24 h after it finishes (keeps the cluster tidy).
            ttl_seconds_after_finished=86400,
            template=k8s.V1PodTemplateSpec(
                metadata=k8s.V1ObjectMeta(
                    labels={"app": "promptforge-exec", "promptforge-job-id": job.job_id},
                ),
                spec=k8s.V1PodSpec(
                    restart_policy="OnFailure",
                    service_account_name="promptforge-exec",
                    containers=[
                        k8s.V1Container(
                            name="execution",
                            image=os.environ["EXECUTION_IMAGE"],
                            env=env_vars,
                            resources=k8s.V1ResourceRequirements(
                                requests={"memory": "128Mi", "cpu": "250m"},
                                limits={"memory": "256Mi", "cpu": "500m"},
                            ),
                        )
                    ],
                ),
            ),
        ),
    )
