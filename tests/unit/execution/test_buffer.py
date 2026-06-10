"""
Unit tests for ResultBuffer (Phase 5).
All GCS I/O is mocked.
"""

from __future__ import annotations

import json
import sys
import os
import time
from unittest.mock import call, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../services/execution"))

from buffer import ResultBuffer


def _make_record(i: int) -> dict:
    return {"prompt_id": i, "response": f"answer {i}", "tokens": {"prompt": 10, "completion": 5}}


class TestCountFlush:
    def test_should_flush_when_count_reaches_500(self):
        buf = ResultBuffer("bucket", "prefix")
        for i in range(499):
            should = buf.add_result(_make_record(i))
        assert not should  # 499 — not yet
        should = buf.add_result(_make_record(500))
        assert should  # 500 — flush now

    def test_flush_results_writes_part_file_and_resets(self):
        buf = ResultBuffer("bucket", "prefix")
        with patch("buffer.write_bytes") as mock_write:
            for i in range(3):
                buf.add_result(_make_record(i))
            path = buf.flush_results()

        assert path == "prefix/results_part_001.jsonl"
        assert mock_write.call_count == 1
        assert buf.result_count == 0

    def test_part_numbers_increment(self):
        buf = ResultBuffer("bucket", "prefix")
        with patch("buffer.write_bytes"):
            buf.add_result(_make_record(1))
            p1 = buf.flush_results()
            buf.add_result(_make_record(2))
            p2 = buf.flush_results()

        assert p1 == "prefix/results_part_001.jsonl"
        assert p2 == "prefix/results_part_002.jsonl"

    def test_flush_returns_none_when_empty(self):
        buf = ResultBuffer("bucket", "prefix")
        assert buf.flush_results() is None


class TestByteFlush:
    def test_should_flush_when_bytes_exceed_50mb(self):
        buf = ResultBuffer("bucket", "prefix")
        # Each record is ~60 bytes; 50MB / 60 = ~870k records
        # Instead, override MAX_BYTES to a small value
        buf.MAX_BYTES = 100
        # Add a record bigger than 100 bytes
        big_record = {"prompt_id": 1, "response": "x" * 200}
        should = buf.add_result(big_record)
        assert should


class TestTimeFlush:
    def test_should_flush_after_30_seconds(self):
        buf = ResultBuffer("bucket", "prefix")
        buf.add_result(_make_record(1))
        # Simulate 31 seconds have passed since last flush
        buf._last_flush_at = time.monotonic() - 31
        should = buf.add_result(_make_record(2))
        assert should

    def test_no_flush_before_30_seconds(self):
        buf = ResultBuffer("bucket", "prefix")
        buf.add_result(_make_record(1))
        buf._last_flush_at = time.monotonic()  # just now
        should = buf.add_result(_make_record(2))
        assert not should


class TestFinalFlush:
    def test_final_flush_writes_results_final(self):
        buf = ResultBuffer("bucket", "prefix")
        buf.add_result(_make_record(1))
        buf.add_result(_make_record(2))

        with patch("buffer.write_bytes") as mock_write, \
             patch("buffer.stream_read", side_effect=Exception("not found")):
            buf.final_flush()

        written_path = mock_write.call_args_list[0][0][1]
        assert written_path == "prefix/results_final.jsonl"
        assert buf.result_count == 0

    def test_final_flush_writes_errors_jsonl(self):
        buf = ResultBuffer("bucket", "prefix")
        buf.add_error({"prompt_id": 1, "error": "timeout"})

        with patch("buffer.write_bytes") as mock_write, \
             patch("buffer.stream_read", side_effect=Exception("not found")):
            buf.final_flush()

        written_path = mock_write.call_args_list[0][0][1]
        assert written_path == "prefix/errors.jsonl"

    def test_final_flush_appends_to_existing_errors(self):
        buf = ResultBuffer("bucket", "prefix")
        existing = json.dumps({"prompt_id": 0, "error": "validation"}).encode()
        buf.add_error({"prompt_id": 1, "error": "timeout"})

        with patch("buffer.write_bytes") as mock_write, \
             patch("buffer.stream_read", return_value=iter([existing])):
            buf.flush_errors()

        written_data = mock_write.call_args[0][2].decode()
        assert '"prompt_id": 0' in written_data
        assert '"prompt_id": 1' in written_data

    def test_final_flush_does_nothing_when_empty(self):
        buf = ResultBuffer("bucket", "prefix")
        with patch("buffer.write_bytes") as mock_write:
            buf.final_flush()
        mock_write.assert_not_called()
