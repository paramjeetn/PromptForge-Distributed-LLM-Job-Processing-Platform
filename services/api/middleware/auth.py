import os
from typing import Annotated

import httpx
from fastapi import Header, HTTPException

# Unkey API endpoint for key verification
_UNKEY_VERIFY_URL = "https://api.unkey.dev/v1/keys.verifyKey"


async def verify_api_key(x_api_key: Annotated[str | None, Header()] = None) -> str:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")

    root_key = os.environ["UNKEY_ROOT_KEY"]
    api_id = os.environ["UNKEY_API_ID"]

    async with httpx.AsyncClient() as client:
        response = await client.post(
            _UNKEY_VERIFY_URL,
            headers={"Authorization": f"Bearer {root_key}"},
            json={"key": x_api_key, "apiId": api_id},
            timeout=5.0,
        )

    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid API key")

    data = response.json()

    if not data.get("valid"):
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")

    # ownerId is set when the key is created in Unkey — this becomes client_id
    client_id = data.get("ownerId")
    if not client_id:
        raise HTTPException(status_code=401, detail="API key has no owner — set ownerId in Unkey")

    return client_id
