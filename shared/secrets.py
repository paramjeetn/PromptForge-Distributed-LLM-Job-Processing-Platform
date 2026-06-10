from __future__ import annotations

import os


def fetch_api_key(api_key_ref: str | None) -> str:
    """
    Return the LLM provider API key.

    Production: `api_key_ref` is a Secret Manager resource name like
        projects/promptforge-1212/secrets/openai-key/versions/latest
    Local / integration tests: leave API_KEY_REF empty and set API_KEY env var instead.
    """
    if api_key_ref:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        response = client.access_secret_version(name=api_key_ref)
        return response.payload.data.decode("utf-8").strip()

    key = os.environ.get("API_KEY", "")
    if not key:
        raise RuntimeError(
            "No API key available: set API_KEY_REF (Secret Manager resource name) "
            "or API_KEY env var for local use."
        )
    return key
