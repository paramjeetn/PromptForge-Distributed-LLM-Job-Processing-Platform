from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Iterator

import google.auth
import google.auth.impersonated_credentials
import google.auth.transport.requests
from google.cloud import storage

_storage_client: storage.Client | None = None


def _get_client() -> storage.Client:
    global _storage_client
    if _storage_client is None:
        _storage_client = storage.Client()
    return _storage_client


def generate_signed_upload_url(
    bucket_name: str,
    blob_path: str,
    expiry_minutes: int = 15,
) -> tuple[str, datetime]:
    credentials = _get_signing_credentials()
    blob = _get_client().bucket(bucket_name).blob(blob_path)
    expiry = timedelta(minutes=expiry_minutes)

    url = blob.generate_signed_url(
        version="v4",
        expiration=expiry,
        method="PUT",
        content_type="application/x-ndjson",
        service_account_email=credentials.service_account_email,
        access_token=credentials.token,
    )

    expires_at = datetime.now(timezone.utc) + expiry
    return url, expires_at


def stream_read(bucket_name: str, blob_path: str) -> Iterator[bytes]:
    blob = _get_client().bucket(bucket_name).blob(blob_path)
    with blob.open("rb") as f:
        for line in f:
            yield line


def write_bytes(bucket_name: str, blob_path: str, data: bytes, content_type: str = "application/x-ndjson") -> None:
    blob = _get_client().bucket(bucket_name).blob(blob_path)
    blob.upload_from_string(data, content_type=content_type)


def delete_blob(bucket_name: str, blob_path: str) -> None:
    _get_client().bucket(bucket_name).blob(blob_path).delete()


def list_result_files(bucket_name: str, prefix: str) -> list[str]:
    """
    Return sorted GCS paths for all results_*.jsonl files under a prefix.
    E.g. prefix="client-id/job-id/" returns paths like
         ["client-id/job-id/results_part_001.jsonl", "client-id/job-id/results_final.jsonl"]
    """
    blobs = _get_client().bucket(bucket_name).list_blobs(prefix=prefix)
    paths = [
        b.name for b in blobs
        if b.name.endswith(".jsonl") and "/results_" in b.name
    ]
    return sorted(paths)


def _get_signing_credentials():
    """Return credentials that can sign GCS URLs (service account or impersonated)."""
    credentials, project = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)

    if not hasattr(credentials, "service_account_email"):
        sa = os.environ.get(
            "SIGNING_SERVICE_ACCOUNT",
            f"promptforge-api@{project or 'promptforge-1212'}.iam.gserviceaccount.com",
        )
        credentials = google.auth.impersonated_credentials.Credentials(
            source_credentials=credentials,
            target_principal=sa,
            target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        credentials.refresh(auth_req)

    return credentials


def generate_signed_download_url(
    bucket_name: str,
    blob_path: str,
    expiry_minutes: int = 60,
) -> tuple[str, datetime]:
    """Generate a signed GET URL for downloading a GCS blob."""
    credentials = _get_signing_credentials()
    blob = _get_client().bucket(bucket_name).blob(blob_path)
    expiry = timedelta(minutes=expiry_minutes)

    url = blob.generate_signed_url(
        version="v4",
        expiration=expiry,
        method="GET",
        service_account_email=credentials.service_account_email,
        access_token=credentials.token,
    )

    expires_at = datetime.now(timezone.utc) + expiry
    return url, expires_at
