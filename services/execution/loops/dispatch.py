"""
Core dispatch loop — integrates Phases 4, 5, and 6.

Phase 4 — Baseline execution:
  Stream prompts from GCS, call LLM via LiteLLM, collect results.

Phase 5 — Result buffer:
  Batch GCS writes (flush every 500 results / 50 MB / 30 s).

Phase 6 — Rate learning:
  Start at rpm=10, slow-start doubles every 30s, backoff on 429.
  TPM ceiling auto-adjusts from rolling p95 token size.

Exit condition: stream EOF + all in-flight tasks done + retry queue empty.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import litellm

from shared.gcs import stream_read
from services.execution.config import JobConfig
from services.execution.buffer import ResultBuffer
from services.execution.rate.controller import RateController


@dataclass
class _PromptItem:
    prompt_id: int | str
    prompt: str
    attempt: int = 0


@dataclass
class DispatchResult:
    completed: int = 0
    failed: int = 0
    parts_written: list[str] = field(default_factory=list)


# Errors that warrant a retry (up to max_retries)
_RETRYABLE = (
    litellm.RateLimitError,
    litellm.ServiceUnavailableError,
    litellm.APIConnectionError,
    litellm.Timeout,
    litellm.InternalServerError,
)


def _error_record(item: _PromptItem, error: str) -> dict:
    return {
        "prompt_id": item.prompt_id,
        "stage": "execution",
        "error": error,
        "attempts": item.attempt + 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def _gcs_line_stream(bucket: str, path: str):
    """
    Async generator: yields stripped non-empty lines from a GCS blob.
    Reads in a background thread so the event loop is never blocked.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=200)

    def _reader() -> None:
        try:
            for raw in stream_read(bucket, path):
                asyncio.run_coroutine_threadsafe(queue.put(raw), loop).result()
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

    threading.Thread(target=_reader, daemon=True).start()

    while True:
        raw = await queue.get()
        if raw is None:
            break
        line = (raw.decode() if isinstance(raw, bytes) else raw).strip()
        if line:
            yield line


async def run(config: JobConfig, api_key: str) -> DispatchResult:
    """
    Main execution loop.  Call this from main.py after updating Firestore to PROCESSING.
    Writes results/errors to GCS and returns a DispatchResult.
    """
    rate = RateController(rpm_cap=config.rpm_limit, tpm_limit=config.tpm_limit)
    buffer = ResultBuffer(config.output_bucket, config.output_prefix)
    result = DispatchResult()

    in_flight: set[asyncio.Task] = set()
    retry_deque: deque[_PromptItem] = deque()

    # Suppress litellm's noisy stdout logging
    litellm.suppress_debug_info = True
    litellm.set_verbose = False

    async def _handle(item: _PromptItem) -> None:
        # Wait out any active 429 cooldown before sending
        if rate.in_cooldown:
            await asyncio.sleep(rate.cooldown_remaining)

        print(f"[dispatch] calling LLM prompt_id={item.prompt_id} attempt={item.attempt+1}", flush=True)
        try:
            t0 = time.monotonic()
            # Run in thread pool so the event loop is not blocked while waiting
            # for the HTTP response (litellm Gemini uses sync gRPC under the hood).
            resp = await asyncio.to_thread(
                litellm.completion,
                model=f"{config.provider}/{config.model}",
                messages=[{"role": "user", "content": item.prompt}],
                api_key=api_key,
                timeout=30,
            )
            latency_ms = int((time.monotonic() - t0) * 1000)

            usage = getattr(resp, "usage", None)
            completion_tokens = getattr(usage, "completion_tokens", 0) or 0
            prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0

            record = {
                "prompt_id": item.prompt_id,
                "response": resp.choices[0].message.content,
                "model": config.model,
                "provider": config.provider,
                "tokens": {
                    "prompt": prompt_tokens,
                    "completion": completion_tokens,
                },
                "latency_ms": latency_ms,
                "attempt": item.attempt + 1,
            }

            should_flush = buffer.add_result(record)
            if should_flush:
                path = buffer.flush_results()
                if path:
                    result.parts_written.append(path)

            result.completed += 1
            rate.record_success(completion_tokens)
            print(f"[dispatch] prompt_id={item.prompt_id} OK latency={latency_ms}ms tokens={completion_tokens}", flush=True)

        except litellm.RateLimitError:
            rate.record_429()
            item.attempt += 1
            if item.attempt < config.max_retries:
                retry_deque.append(item)
            else:
                buffer.add_error(_error_record(item, "rate_limit_exceeded"))
                result.failed += 1

        except _RETRYABLE as exc:
            item.attempt += 1
            if item.attempt < config.max_retries:
                retry_deque.append(item)
            else:
                buffer.add_error(_error_record(item, str(exc)))
                result.failed += 1

        except Exception as exc:
            # Non-retryable (400, 401, 403, bad request, etc.)
            print(f"[dispatch] prompt_id={item.prompt_id} ERROR (non-retryable): {exc}", flush=True)
            buffer.add_error(_error_record(item, str(exc)))
            result.failed += 1

    def _spawn(item: _PromptItem) -> None:
        task = asyncio.create_task(_handle(item))
        in_flight.add(task)
        task.add_done_callback(in_flight.discard)

    async def _drain_retries() -> None:
        """Dispatch all current retry-queue items before advancing stream."""
        while retry_deque:
            _spawn(retry_deque.popleft())
            await asyncio.sleep(rate.interval)

    # ── Main dispatch loop ────────────────────────────────────────────
    async for raw_line in _gcs_line_stream(config.input_bucket, config.prompts_path):
        # Drain retry queue first (retries have priority)
        await _drain_retries()

        data = json.loads(raw_line)
        _spawn(_PromptItem(prompt_id=data["prompt_id"], prompt=data["prompt"]))
        await asyncio.sleep(rate.interval)

    # ── Wait for all in-flight tasks to finish ────────────────────────
    while in_flight:
        await asyncio.sleep(0.05)

    # ── Process any retries generated after stream exhausted ──────────
    while retry_deque:
        await _drain_retries()
        while in_flight:
            await asyncio.sleep(0.05)

    # ── Final buffer flush ────────────────────────────────────────────
    buffer.final_flush()

    return result
