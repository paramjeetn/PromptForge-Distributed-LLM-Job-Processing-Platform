from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterator

import google.auth
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
    # Refresh credentials so the access token is current for signing.
    # On Cloud Run / GKE the default credentials carry a service_account_email
    # which is required for V4 signed URLs without a key file.
    credentials, _ = google.auth.default()
    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)

    client = _get_client()
    blob = client.bucket(bucket_name).blob(blob_path)
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
