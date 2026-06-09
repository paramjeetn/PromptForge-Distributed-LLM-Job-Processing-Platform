import sys
import os

# Allow imports from project root (shared/) and services/launcher/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../services/launcher"))

import json
import pytest
from unittest.mock import call, patch

from validator import InvalidFileError, ValidationResult, validate_prompts

INPUT_BUCKET = "test-input"
OUTPUT_BUCKET = "test-output"
BLOB_PATH = "client_abc/job_123/prompts.jsonl"
OUTPUT_PREFIX = "client_abc/job_123"


def _lines(*dicts) -> list[bytes]:
    """Build a list of newline-terminated JSONL byte lines."""
    return [json.dumps(d).encode() + b"\n" for d in dicts]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_all_valid_lines():
    lines = _lines(
        {"prompt_id": 1, "prompt": "Hello"},
        {"prompt_id": 2, "prompt": "World"},
        {"prompt_id": 3, "prompt": "Foo"},
    )
    with (
        patch("validator.stream_read", return_value=iter(lines)),
        patch("validator.write_bytes") as mock_write,
    ):
        result = validate_prompts(INPUT_BUCKET, BLOB_PATH, OUTPUT_BUCKET, OUTPUT_PREFIX)

    assert result.prompt_count == 3
    assert result.invalid_count == 0
    mock_write.assert_not_called()


def test_empty_file():
    with (
        patch("validator.stream_read", return_value=iter([])),
        patch("validator.write_bytes") as mock_write,
    ):
        result = validate_prompts(INPUT_BUCKET, BLOB_PATH, OUTPUT_BUCKET, OUTPUT_PREFIX)

    assert result.prompt_count == 0
    assert result.invalid_count == 0
    mock_write.assert_not_called()


def test_blank_lines_are_skipped():
    lines = [b"\n", b"   \n", json.dumps({"prompt_id": 1, "prompt": "hi"}).encode() + b"\n", b"\n"]
    with (
        patch("validator.stream_read", return_value=iter(lines)),
        patch("validator.write_bytes") as mock_write,
    ):
        result = validate_prompts(INPUT_BUCKET, BLOB_PATH, OUTPUT_BUCKET, OUTPUT_PREFIX)

    assert result.prompt_count == 1
    assert result.invalid_count == 0
    mock_write.assert_not_called()


# ---------------------------------------------------------------------------
# Mixed valid / invalid
# ---------------------------------------------------------------------------

def test_mixed_valid_and_invalid():
    lines = _lines(
        {"prompt_id": 1, "prompt": "Hello"},
        {"bad_key": "no prompt_id here"},
        {"prompt_id": 3, "prompt": "World"},
    )
    with (
        patch("validator.stream_read", return_value=iter(lines)),
        patch("validator.write_bytes") as mock_write,
    ):
        result = validate_prompts(INPUT_BUCKET, BLOB_PATH, OUTPUT_BUCKET, OUTPUT_PREFIX)

    assert result.prompt_count == 2
    assert result.invalid_count == 1
    # errors.jsonl must be written once
    mock_write.assert_called_once()
    dest_path = mock_write.call_args[0][1]
    assert dest_path == f"{OUTPUT_PREFIX}/errors.jsonl"


def test_missing_prompt_id_only_line_raises():
    """A file where every line is invalid (missing required field) → InvalidFileError."""
    lines = _lines({"prompt": "no id"})
    with (
        patch("validator.stream_read", return_value=iter(lines)),
        patch("validator.write_bytes"),
    ):
        with pytest.raises(InvalidFileError):
            validate_prompts(INPUT_BUCKET, BLOB_PATH, OUTPUT_BUCKET, OUTPUT_PREFIX)


def test_not_json_raises():
    lines = [b"this is not json at all\n", b"neither is this\n"]
    with (
        patch("validator.stream_read", return_value=iter(lines)),
        patch("validator.write_bytes"),
    ):
        with pytest.raises(InvalidFileError):
            validate_prompts(INPUT_BUCKET, BLOB_PATH, OUTPUT_BUCKET, OUTPUT_PREFIX)


def test_error_record_structure():
    """Each error line written to GCS must be valid JSON with the expected fields."""
    lines = _lines({"bad": "no prompt_id"})
    captured: list[bytes] = []

    def capture_write(bucket, path, data):
        captured.append(data)

    with (
        patch("validator.stream_read", return_value=iter(lines)),
        patch("validator.write_bytes", side_effect=capture_write),
    ):
        with pytest.raises(InvalidFileError):
            validate_prompts(INPUT_BUCKET, BLOB_PATH, OUTPUT_BUCKET, OUTPUT_PREFIX)

    assert captured, "write_bytes should have been called with error data"
    first_line = captured[0].splitlines()[0]
    record = json.loads(first_line)
    assert record["prompt_id"] is None
    assert record["stage"] == "validation"
    assert "error" in record
    assert "line" in record


def test_returns_validation_result_type():
    lines = _lines({"prompt_id": 1, "prompt": "test"})
    with (
        patch("validator.stream_read", return_value=iter(lines)),
        patch("validator.write_bytes"),
    ):
        result = validate_prompts(INPUT_BUCKET, BLOB_PATH, OUTPUT_BUCKET, OUTPUT_PREFIX)

    assert isinstance(result, ValidationResult)
