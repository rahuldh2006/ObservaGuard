"""
Unit tests for app.anomaly_detector — Z-score computation, severity mapping,
cooldown logic, and the full detect_anomalies pipeline.
"""

from datetime import datetime, timedelta

import pytest

from app.anomaly_detector import (
    _compute_z_score,
    _severity_from_z,
    _alert_in_cooldown,
    record_metric_snapshot,
    detect_anomalies,
    Z_SCORE_THRESHOLD,
    ERROR_RATE_THRESHOLD,
    CRITICAL_BURST_N,
)
from app.models import AlertEvent, LogEntry


# ── _compute_z_score ──────────────────────────────────────────────────────────

class TestComputeZScore:
    def test_returns_zero_for_empty_history(self):
        assert _compute_z_score(0.5, []) == 0.0

    def test_returns_zero_for_one_element(self):
        assert _compute_z_score(0.5, [0.1]) == 0.0

    def test_returns_zero_for_two_elements(self):
        assert _compute_z_score(0.5, [0.1, 0.2]) == 0.0

    def test_returns_zero_when_history_is_constant(self):
        # sigma = 0 → avoid division by zero, return 0
        assert _compute_z_score(0.9, [0.5, 0.5, 0.5, 0.5]) == 0.0

    def test_positive_z_when_value_above_mean(self):
        history = [0.05, 0.06, 0.05, 0.07, 0.05]
        z = _compute_z_score(0.80, history)
        assert z > Z_SCORE_THRESHOLD   # should flag as a spike

    def test_negative_z_when_value_below_mean(self):
        history = [0.80, 0.82, 0.79, 0.81, 0.80, 0.83]
        z = _compute_z_score(0.05, history)
        assert z < 0

    def test_near_zero_z_for_value_near_mean(self):
        history = [0.10, 0.12, 0.09, 0.11, 0.10]
        z = _compute_z_score(0.105, history)
        assert abs(z) < 1.0

    def test_minimum_three_history_points_required(self):
        assert _compute_z_score(0.9, [0.1, 0.1]) == 0.0
        # 3 identical values → sigma 0 → still 0
        assert _compute_z_score(0.9, [0.1, 0.1, 0.1]) == 0.0
        # 3 varied values → should return non-trivial result
        z = _compute_z_score(0.9, [0.05, 0.06, 0.07])
        assert z != 0.0


# ── _severity_from_z ──────────────────────────────────────────────────────────

class TestSeverityFromZ:
    def test_critical_high_z(self):
        assert _severity_from_z(4.5, 0.3) == "CRITICAL"

    def test_critical_high_error_rate(self):
        assert _severity_from_z(1.0, 0.80) == "CRITICAL"

    def test_high_z(self):
        assert _severity_from_z(3.1, 0.3) == "HIGH"

    def test_high_error_rate(self):
        assert _severity_from_z(1.0, 0.60) == "HIGH"

    def test_medium_at_threshold_z(self):
        assert _severity_from_z(2.5, 0.10) == "MEDIUM"

    def test_medium_at_threshold_rate(self):
        assert _severity_from_z(0.0, 0.35) == "MEDIUM"

    def test_low_below_all_thresholds(self):
        assert _severity_from_z(1.0, 0.10) == "LOW"

    def test_low_zero_z_low_rate(self):
        assert _severity_from_z(0.0, 0.0) == "LOW"


# ── _alert_in_cooldown ────────────────────────────────────────────────────────

class TestAlertCooldown:
    def test_no_cooldown_when_db_empty(self, db_session):
        now = datetime.utcnow()
        assert _alert_in_cooldown(db_session, "api-gw", "ERROR_SPIKE", now) is False

    def test_cooldown_active_after_recent_alert(self, db_session):
        now = datetime.utcnow()
        alert = AlertEvent(
            triggered_at=now - timedelta(seconds=30),
            service="api-gw",
            alert_type="ERROR_SPIKE",
            severity="HIGH",
            error_rate=0.5,
            z_score=3.0,
            window_errors=15,
            window_total=30,
            message="test",
        )
        db_session.add(alert)
        db_session.commit()
        assert _alert_in_cooldown(db_session, "api-gw", "ERROR_SPIKE", now) is True

    def test_cooldown_expired(self, db_session):
        now = datetime.utcnow()
        alert = AlertEvent(
            triggered_at=now - timedelta(seconds=300),  # well past cooldown
            service="api-gw",
            alert_type="ERROR_SPIKE",
            severity="HIGH",
            error_rate=0.5,
            z_score=3.0,
            window_errors=15,
            window_total=30,
            message="test",
        )
        db_session.add(alert)
        db_session.commit()
        assert _alert_in_cooldown(db_session, "api-gw", "ERROR_SPIKE", now) is False

    def test_different_alert_type_not_in_cooldown(self, db_session):
        now = datetime.utcnow()
        alert = AlertEvent(
            triggered_at=now - timedelta(seconds=10),
            service="api-gw",
            alert_type="ERROR_SPIKE",
            severity="HIGH",
            error_rate=0.5,
            z_score=3.0,
            window_errors=15,
            window_total=30,
            message="test",
        )
        db_session.add(alert)
        db_session.commit()
        # CRITICAL_BURST has its own cooldown — should not be blocked
        assert _alert_in_cooldown(db_session, "api-gw", "CRITICAL_BURST", now) is False


# ── record_metric_snapshot ────────────────────────────────────────────────────

class TestRecordMetricSnapshot:
    def _insert_logs(self, db, service, levels, minutes_ago=0):
        ts = datetime.utcnow() - timedelta(minutes=minutes_ago)
        for level in levels:
            db.add(LogEntry(
                timestamp=ts, service=service,
                level=level, message="test", raw="test",
            ))
        db.commit()

    def test_snapshot_created(self, db_session):
        self._insert_logs(db_session, "svc", ["INFO", "ERROR"])
        snap = record_metric_snapshot(db_session, "svc")
        assert snap.id is not None
        assert snap.service == "svc"
        assert snap.total_logs == 2
        assert snap.error_count == 1

    def test_error_rate_calculated(self, db_session):
        self._insert_logs(db_session, "svc", ["ERROR", "ERROR", "INFO", "INFO"])
        snap = record_metric_snapshot(db_session, "svc")
        assert abs(snap.error_rate - 0.5) < 0.01

    def test_no_logs_produces_zero_rate(self, db_session):
        snap = record_metric_snapshot(db_session, "empty-svc")
        assert snap.error_rate == 0.0
        assert snap.total_logs == 0


# ── detect_anomalies ──────────────────────────────────────────────────────────

class TestDetectAnomalies:
    def _insert_logs(self, db, service, levels, minutes_ago=0):
        ts = datetime.utcnow() - timedelta(minutes=minutes_ago, seconds=10)
        for level in levels:
            db.add(LogEntry(
                timestamp=ts, service=service,
                level=level, message="test", raw="test",
            ))
        db.commit()

    def test_no_alerts_for_normal_traffic(self, db_session):
        # Mostly INFO logs — should not trigger anything
        self._insert_logs(db_session, "svc", ["INFO"] * 9 + ["ERROR"])
        alerts = detect_anomalies(db_session, "svc")
        assert alerts == []

    def test_error_rate_high_alert_fired(self, db_session):
        # 80% error rate exceeds ERROR_RATE_THRESHOLD (0.35)
        self._insert_logs(db_session, "svc", ["ERROR"] * 8 + ["INFO"] * 2)
        alerts = detect_anomalies(db_session, "svc")
        types = [a.alert_type for a in alerts]
        assert "ERROR_RATE_HIGH" in types

    def test_critical_burst_alert_fired(self, db_session):
        # CRITICAL_BURST_N (3) or more CRITICAL logs in the window
        self._insert_logs(db_session, "svc", ["CRITICAL"] * CRITICAL_BURST_N + ["INFO"] * 5)
        alerts = detect_anomalies(db_session, "svc")
        types = [a.alert_type for a in alerts]
        assert "CRITICAL_BURST" in types

    def test_no_duplicate_alerts_within_cooldown(self, db_session):
        # Fire once
        self._insert_logs(db_session, "svc", ["ERROR"] * 8 + ["INFO"] * 2)
        first = detect_anomalies(db_session, "svc")
        # Fire again immediately — cooldown should suppress
        second = detect_anomalies(db_session, "svc")
        # First should have alerts, second should be empty (cooldown)
        assert len(first) > 0
        assert len(second) == 0
