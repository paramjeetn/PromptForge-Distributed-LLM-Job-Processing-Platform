from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pydantic import ValidationError

from shared.gcs import stream_read, write_bytes
from shared.models.prompt import PromptSchema


class InvalidFileError(Exception):
    """Raised when the file cannot be read as JSONL at all (binary or fully unparseable)."""


@dataclass
class ValidationResult:
    prompt_count: int
    invalid_count: int


def validate_prompts(
    input_bucket: str,
    blob_path: str,
    output_bucket: str,
    output_prefix: str,
) -> ValidationResult:
    """
    Stream-validate prompts.jsonl line by line against PromptSchema.

    Valid lines increment prompt_count.
    Invalid lines are written immediately to {output_prefix}/errors.jsonl
    and increment invalid_count.

    Raises InvalidFileError if the file has content but every single line
    failed to parse (file is not JSONL at all).
    """
    prompt_count = 0
    invalid_count = 0
    error_lines: list[bytes] = []

    for line_bytes in stream_read(input_bucket, blob_path):
        try:
            line_str = line_bytes.decode("utf-8").strip()
        except UnicodeDecodeError:
            invalid_count += 1
            _append_error(error_lines, line_num=prompt_count + invalid_count, raw=repr(line_bytes), error="UnicodeDecodeError")
            continue

        if not line_str:
            continue

        try:
            PromptSchema.model_validate_json(line_str)
            prompt_count += 1
        except (ValidationError, json.JSONDecodeError, ValueError) as exc:
            invalid_count += 1
            _append_error(
                error_lines,
                line_num=prompt_count + invalid_count,
                raw=line_str,
                error=str(exc),
            )

    # Flush error lines to GCS in one write
    if error_lines:
        errors_path = f"{output_prefix}/errors.jsonl"
        write_bytes(output_bucket, errors_path, b"".join(error_lines))

    # If the file had content but no line was valid JSON, it is not JSONL
    if prompt_count == 0 and invalid_count > 0:
        raise InvalidFileError(
            f"No valid JSONL lines found in {blob_path} ({invalid_count} lines failed to parse)"
        )

    return ValidationResult(prompt_count=prompt_count, invalid_count=invalid_count)


def _append_error(
    error_lines: list[bytes],
    line_num: int,
    raw: str,
    error: str,
) -> None:
    record = {
        "prompt_id": None,
        "line": line_num,
        "raw": raw[:200],  # cap to avoid bloat
        "error": error,
        "stage": "validation",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    error_lines.append(json.dumps(record).encode() + b"\n")
