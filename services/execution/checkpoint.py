"""
Checkpoint — Phase 7.

Writes state.json to GCS every 30s so a pod crash can resume from the last
saved offset instead of reprocessing prompts from the beginning.

State written:
  {
    "completed": 150,
    "failed": 3,
    "part_num": 1,
    "rate": { ...RateController snapshot... },
    "checkpoint_at": "2024-01-01T12:00:00Z"
  }

Resume logic (in dispatch.run):
  - Load state.json on startup
  - Skip first (completed + failed) lines from the stream
  - Restore buffer part_num and rate controller state
"""

from __future__ import annotations

import json
import sys
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.gcs import stream_read, write_bytes


INTERVAL = 30.0  # seconds between checkpoint saves


@dataclass
class CheckpointData:
    completed: int
    failed: int
    part_num: int
    rate: dict
    checkpoint_at: str


class Checkpoint:
    def __init__(self, output_bucket: str, output_prefix: str) -> None:
        self.bucket = output_bucket
        self._path = f"{output_prefix}/state.json"
        self._last_saved_at: float = time.monotonic()

    def load(self) -> Optional[CheckpointData]:
        """
        Load state from GCS. Returns None if no checkpoint exists or it's unreadable.
        """
        try:
            raw = b"".join(stream_read(self.bucket, self._path))
            data = json.loads(raw)
            return CheckpointData(
                completed=data["completed"],
                failed=data["failed"],
                part_num=data["part_num"],
                rate=data.get("rate", {}),
                checkpoint_at=data.get("checkpoint_at", ""),
            )
        except Exception:
            return None

    def should_save(self) -> bool:
        return (time.monotonic() - self._last_saved_at) >= INTERVAL

    def save(self, completed: int, failed: int, part_num: int, rate: dict) -> None:
        data = {
            "completed": completed,
            "failed": failed,
            "part_num": part_num,
            "rate": rate,
            "checkpoint_at": datetime.now(timezone.utc).isoformat(),
        }
        write_bytes(
            self.bucket,
            self._path,
            json.dumps(data).encode(),
            content_type="application/json",
        )
        self._last_saved_at = time.monotonic()
        print(f"[checkpoint] saved completed={completed} failed={failed} part_num={part_num}", flush=True)
