"""
Result buffer — Phase 5.

Batches LLM responses in memory and flushes to GCS when any of these
conditions is met:
  - 500 results accumulated
  - 50 MB of buffered data
  - 30 seconds since last flush

Part files are numbered sequentially: results_part_001.jsonl, _002, ...
The final remainder is written as results_final.jsonl at job completion.

Errors are buffered separately and written (or appended) to errors.jsonl.
"""

from __future__ import annotations

import json
import time

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.gcs import stream_read, write_bytes


class ResultBuffer:
    MAX_COUNT = 500
    MAX_BYTES = 50 * 1024 * 1024  # 50 MB
    FLUSH_INTERVAL = 30.0          # seconds

    def __init__(self, output_bucket: str, output_prefix: str) -> None:
        self.output_bucket = output_bucket
        self.output_prefix = output_prefix

        self._results: list[dict] = []
        self._errors: list[dict] = []
        self._result_bytes = 0
        self._part_num = 0
        self._last_flush_at = time.monotonic()

    # ------------------------------------------------------------------
    # Adding records
    # ------------------------------------------------------------------

    def add_result(self, record: dict) -> bool:
        """
        Buffer a successful result.
        Returns True if the buffer should be flushed now (caller decides when
        to actually call flush_results()).
        """
        self._results.append(record)
        self._result_bytes += len(json.dumps(record, ensure_ascii=False))
        return self._should_flush()

    def add_error(self, record: dict) -> None:
        """Buffer an error record (flushed at job completion or manually)."""
        self._errors.append(record)

    # ------------------------------------------------------------------
    # Flushing
    # ------------------------------------------------------------------

    def flush_results(self) -> str | None:
        """
        Write buffered results to a new part file in GCS.
        Returns the GCS path written, or None if buffer was empty.
        """
        if not self._results:
            return None

        self._part_num += 1
        path = f"{self.output_prefix}/results_part_{self._part_num:03d}.jsonl"
        data = "\n".join(json.dumps(r, ensure_ascii=False) for r in self._results).encode()
        write_bytes(self.output_bucket, path, data)

        self._results.clear()
        self._result_bytes = 0
        self._last_flush_at = time.monotonic()
        return path

    def flush_errors(self) -> str | None:
        """
        Append buffered errors to errors.jsonl in GCS.

        If errors.jsonl already exists (written by the validator in Phase 3),
        read existing content and prepend it so execution errors are appended.
        Returns the GCS path written, or None if buffer was empty.
        """
        if not self._errors:
            return None

        path = f"{self.output_prefix}/errors.jsonl"
        new_lines = "\n".join(json.dumps(e, ensure_ascii=False) for e in self._errors)

        # Try to append to existing file
        try:
            existing = b"".join(stream_read(self.output_bucket, path))
            existing_text = existing.decode().rstrip("\n")
            combined = (existing_text + "\n" + new_lines).encode()
        except Exception:
            combined = new_lines.encode()

        write_bytes(self.output_bucket, path, combined)
        self._errors.clear()
        return path

    def final_flush(self) -> None:
        """
        Called at job completion. Flushes remaining results as results_final.jsonl
        and writes all accumulated errors.
        """
        if self._results:
            path = f"{self.output_prefix}/results_final.jsonl"
            data = "\n".join(json.dumps(r, ensure_ascii=False) for r in self._results).encode()
            write_bytes(self.output_bucket, path, data)
            self._results.clear()
            self._result_bytes = 0

        self.flush_errors()

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    @property
    def result_count(self) -> int:
        return len(self._results)

    @property
    def error_count(self) -> int:
        return len(self._errors)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _should_flush(self) -> bool:
        return (
            len(self._results) >= self.MAX_COUNT
            or self._result_bytes >= self.MAX_BYTES
            or (time.monotonic() - self._last_flush_at) >= self.FLUSH_INTERVAL
        )
