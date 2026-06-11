from __future__ import annotations

import json
import os
import time
from typing import Annotated

from fastapi import Header, HTTPException
from google.cloud import secretmanager

# Secret Manager secret name: projects/{project}/secrets/promptforge-api-keys/versions/latest
# Contents: JSON object mapping api_key -> client_id
# e.g. {"<api-key>": "client-id"}

_cache: dict[str, str] = {}
_cache_loaded_at: float = 0.0
_CACHE_TTL = 300  # 5 minutes


def _load_keys() -> dict[str, str]:
    global _cache, _cache_loaded_at

    now = time.monotonic()
    if _cache and (now - _cache_loaded_at) < _CACHE_TTL:
        return _cache

    project = os.environ.get("GCP_PROJECT_ID", "")
    secret_name = f"projects/{project}/secrets/promptforge-api-keys/versions/latest"

    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(name=secret_name)
    keys = json.loads(response.payload.data.decode())

    _cache = keys
    _cache_loaded_at = now
    return keys


async def verify_api_key(x_api_key: Annotated[str | None, Header()] = None) -> str:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")

    try:
        keys = _load_keys()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Auth config error: {exc}") from exc

    client_id = keys.get(x_api_key)
    if not client_id:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")

    return client_id
