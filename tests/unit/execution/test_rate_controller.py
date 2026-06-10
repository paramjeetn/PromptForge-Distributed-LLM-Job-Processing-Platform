"""
Unit tests for the rate controller (Phase 6).
No I/O — pure logic.
"""

import time
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../services/execution"))

from rate.controller import RateController


class TestSlowStart:
    def test_initial_rpm_target_is_10(self):
        rc = RateController(rpm_cap=100, tpm_limit=0)
        assert rc.rpm_target == 10.0

    def test_initial_mode_is_slow_start(self):
        rc = RateController(rpm_cap=100, tpm_limit=0)
        assert rc.learning_mode == "slow_start"

    def test_slow_start_boosts_by_1_5x(self):
        rc = RateController(rpm_cap=100, tpm_limit=0)
        # Force boost timer to trigger
        rc._last_boost_at = time.monotonic() - 31
        rc.record_success(100)
        assert rc.rpm_target == pytest.approx(15.0)

    def test_rpm_target_capped_at_rpm_cap(self):
        rc = RateController(rpm_cap=12, tpm_limit=0)
        rc._last_boost_at = time.monotonic() - 31
        rc.record_success(100)
        # 10 * 1.5 = 15 but capped at 12
        assert rc.rpm_target == 12.0

    def test_no_boost_before_interval(self):
        rc = RateController(rpm_cap=100, tpm_limit=0)
        rc._last_boost_at = time.monotonic()  # just now
        before = rc.rpm_target
        rc.record_success(100)
        assert rc.rpm_target == before


class TestCongestionAvoidance:
    def test_429_switches_to_congestion_avoidance(self):
        rc = RateController(rpm_cap=100, tpm_limit=0)
        rc.record_429()
        assert rc.learning_mode == "congestion_avoidance"

    def test_429_reduces_rpm_target(self):
        rc = RateController(rpm_cap=100, tpm_limit=0)
        rc.rpm_target = 100.0
        rc.record_429()
        assert rc.rpm_target == pytest.approx(75.0)

    def test_429_returns_cooldown_duration(self):
        rc = RateController(rpm_cap=100, tpm_limit=0)
        duration = rc.record_429()
        assert duration == RateController.COOLDOWN_SECONDS

    def test_in_cooldown_after_429(self):
        rc = RateController(rpm_cap=100, tpm_limit=0)
        rc.record_429()
        assert rc.in_cooldown is True

    def test_congestion_avoidance_additive_increase(self):
        rc = RateController(rpm_cap=100, tpm_limit=0)
        rc.learning_mode = "congestion_avoidance"
        rc.rpm_target = 80.0
        rc._last_boost_at = time.monotonic() - 31
        rc.record_success(100)
        assert rc.rpm_target == pytest.approx(81.0)

    def test_rpm_never_below_1(self):
        rc = RateController(rpm_cap=100, tpm_limit=0)
        rc.rpm_target = 1.0
        rc.record_429()
        assert rc.rpm_target >= 1.0


class TestTPMLearning:
    def test_p95_tokens_updated_after_10_samples(self):
        rc = RateController(rpm_cap=100, tpm_limit=0)
        for i in range(10):
            rc.record_success(100 + i * 10)
        # p95 of [100,110,...,190] = value at index 9 (95th pct of 10 items)
        assert rc.p95_tokens > 100

    def test_effective_rpm_capped_by_tpm(self):
        rc = RateController(rpm_cap=100, tpm_limit=1000)
        rc.p95_tokens = 100.0  # 1000 TPM / 100 tokens = 10 RPM max
        rc.rpm_target = 60.0
        assert rc.effective_rpm == pytest.approx(10.0)

    def test_effective_rpm_uses_rpm_when_tighter(self):
        rc = RateController(rpm_cap=50, tpm_limit=100000)
        rc.p95_tokens = 100.0  # 100000 / 100 = 1000 RPM — looser than rpm_cap=50
        rc.rpm_target = 50.0
        assert rc.effective_rpm == pytest.approx(50.0)

    def test_no_tpm_limit_uses_rpm_only(self):
        rc = RateController(rpm_cap=60, tpm_limit=0)
        rc.rpm_target = 40.0
        assert rc.effective_rpm == pytest.approx(40.0)


class TestStateSnapshot:
    def test_snapshot_contains_key_fields(self):
        rc = RateController(rpm_cap=100, tpm_limit=5000)
        snap = rc.state_snapshot()
        assert "rpm_target" in snap
        assert "p95_tokens" in snap
        assert "learning_mode" in snap

    def test_restore_from_snapshot(self):
        rc = RateController(rpm_cap=100, tpm_limit=0)
        rc.restore({"rpm_target": 75.0, "p95_tokens": 850.0, "learning_mode": "congestion_avoidance"})
        assert rc.rpm_target == 75.0
        assert rc.p95_tokens == 850.0
        assert rc.learning_mode == "congestion_avoidance"


import pytest
