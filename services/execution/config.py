from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class JobConfig:
    job_id: str
    client_id: str
    provider: str
    model: str
    rpm_limit: int
    tpm_limit: int
    max_retries: int
    input_bucket: str
    output_bucket: str
    prompts_path: str
    prompt_count: int
    firestore_project_id: str
    api_key_ref: str | None

    @property
    def output_prefix(self) -> str:
        return f"{self.client_id}/{self.job_id}"


def from_env() -> JobConfig:
    """Build JobConfig from environment variables injected by the launcher."""
    prompts_path = os.environ["PROMPTS_PATH"]
    # Derive client_id from path: "{client_id}/{job_id}/prompts.jsonl"
    client_id = prompts_path.split("/")[0]

    return JobConfig(
        job_id=os.environ["JOB_ID"],
        client_id=client_id,
        provider=os.environ["PROVIDER"],
        model=os.environ["MODEL"],
        rpm_limit=int(os.environ.get("RPM_LIMIT", "60")),
        tpm_limit=int(os.environ.get("TPM_LIMIT", "0")),
        max_retries=int(os.environ.get("MAX_RETRIES", "3")),
        input_bucket=os.environ["INPUT_BUCKET"],
        output_bucket=os.environ["OUTPUT_BUCKET"],
        prompts_path=prompts_path,
        prompt_count=int(os.environ.get("PROMPT_COUNT", "0")),
        firestore_project_id=os.environ["FIRESTORE_PROJECT_ID"],
        api_key_ref=os.environ.get("API_KEY_REF") or None,
    )
