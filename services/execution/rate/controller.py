"""
Rate controller — Phase 6.

Implements the slow-start / congestion-avoidance algorithm described in the
build phases doc. Discovers the real provider RPM/TPM limits without manual
configuration, the same way TCP discovers network capacity.

Slow start:    rpm_target starts at 10, doubles every 30s until first 429.
On 429:        rpm_target *= 0.75, dispatch paused for 60s cooldown.
After 429:     congestion-avoidance mode — rpm_target += 1 every 30s.
TPM learning:  p95 token size updated after every success; effective RPM
               auto-throttles if token budget is the tighter constraint.
"""

from __future__ import annotations

import time
from collections import deque


class RateController:
    # How often (seconds) to boost rpm_target when no 429 seen.
    BOOST_INTERVAL = 30.0
    # Multiplier applied to rpm_target on first 429.
    BACKOFF_FACTOR = 0.75
    # How long to pause dispatch after a 429.
    COOLDOWN_SECONDS = 60.0

    def __init__(self, rpm_cap: int, tpm_limit: int) -> None:
        self.rpm_cap = max(rpm_cap, 1)
        self.tpm_limit = tpm_limit
        self.rpm_target: float = min(10.0, self.rpm_cap)
        self.p95_tokens: float = 1000.0
        self.learning_mode: str = "slow_start"

        # Rolling window of last 1000 completion token counts.
        self._token_history: deque[int] = deque(maxlen=1000)
        self._last_boost_at: float = time.monotonic()
        # When > now(), dispatch should wait.
        self._cooldown_until: float = 0.0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def in_cooldown(self) -> bool:
        return time.monotonic() < self._cooldown_until

    @property
    def cooldown_remaining(self) -> float:
        return max(0.0, self._cooldown_until - time.monotonic())

    @property
    def effective_rpm(self) -> float:
        """RPM respecting both the learned rate and the TPM ceiling."""
        rpm = self.rpm_target
        if self.tpm_limit > 0 and self.p95_tokens > 0:
            tpm_rpm = self.tpm_limit / self.p95_tokens
            rpm = min(rpm, tpm_rpm)
        return min(rpm, self.rpm_cap)

    @property
    def interval(self) -> float:
        """Seconds to sleep between dispatches."""
        return 60.0 / max(self.effective_rpm, 0.1)

    def record_success(self, completion_tokens: int) -> None:
        """Call after every successful LLM response."""
        if completion_tokens > 0:
            self._token_history.append(completion_tokens)
            if len(self._token_history) >= 10:
                sorted_t = sorted(self._token_history)
                idx = max(int(len(sorted_t) * 0.95) - 1, 0)
                self.p95_tokens = max(float(sorted_t[idx]), 1.0)

        if not self.in_cooldown:
            now = time.monotonic()
            if now - self._last_boost_at >= self.BOOST_INTERVAL:
                self._boost()
                self._last_boost_at = now

    def record_429(self) -> float:
        """
        Call when a 429 is received.
        Returns the cooldown duration in seconds (caller should sleep this long
        or check `cooldown_remaining` before dispatching).
        """
        self.rpm_target = max(self.rpm_target * self.BACKOFF_FACTOR, 1.0)
        self.learning_mode = "congestion_avoidance"
        self._cooldown_until = time.monotonic() + self.COOLDOWN_SECONDS
        self._last_boost_at = time.monotonic()  # reset boost timer after backoff
        return self.COOLDOWN_SECONDS

    def state_snapshot(self) -> dict:
        """Return current state for checkpointing (Phase 7)."""
        return {
            "rpm_target": round(self.rpm_target, 2),
            "effective_rpm": round(self.effective_rpm, 2),
            "p95_tokens": round(self.p95_tokens, 1),
            "learning_mode": self.learning_mode,
            "tpm_limit": self.tpm_limit,
        }

    def restore(self, snapshot: dict) -> None:
        """Restore state from a checkpoint (Phase 7)."""
        self.rpm_target = float(snapshot.get("rpm_target", self.rpm_target))
        self.p95_tokens = float(snapshot.get("p95_tokens", self.p95_tokens))
        self.learning_mode = snapshot.get("learning_mode", self.learning_mode)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _boost(self) -> None:
        if self.learning_mode == "slow_start":
            self.rpm_target = min(self.rpm_target * 1.5, self.rpm_cap)
        else:
            # Congestion avoidance: additive increase
            self.rpm_target = min(self.rpm_target + 1.0, self.rpm_cap)
