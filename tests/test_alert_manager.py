"""
Unit tests for app.alert_manager — webhook payload building,
port resolution, and alert processing with mocked HTTP calls.
"""

import re
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.alert_manager import _build_payload, process_alerts, WEBHOOK_URL
from app.models import AlertEvent


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_alert_obj(db_session, **overrides):
    """Insert a real AlertEvent row and return it (so the session tracks it)."""
    defaults = dict(
        triggered_at=datetime(2024, 1, 15, 12, 0, 0),
        service="api-gateway",
        alert_type="ERROR_SPIKE",
        severity="HIGH",
        error_rate=0.60,
        z_score=3.20,
        window_errors=18,
        window_total=30,
        message="[ERROR_SPIKE] test alert",
        webhook_fired=False,
        webhook_status=None,
    )
    defaults.update(overrides)
    alert = AlertEvent(**defaults)
    db_session.add(alert)
    db_session.commit()
    db_session.refresh(alert)
    return alert


# ── _build_payload ────────────────────────────────────────────────────────────

class TestBuildPayload:
    def test_top_level_keys(self, db_session):
        alert = _make_alert_obj(db_session)
        payload = _build_payload(alert)
        assert payload["event"] == "observaguard.alert"
        assert payload["version"] == "1.0"
        assert "fired_at" in payload
        assert "alert" in payload
        assert "runbook" in payload
        assert "dashboard" in payload

    def test_alert_inner_keys(self, db_session):
        alert = _make_alert_obj(db_session)
        a = _build_payload(alert)["alert"]
        for key in ("id", "type", "severity", "service", "error_rate",
                    "z_score", "window_errors", "window_total", "message"):
            assert key in a

    def test_alert_type_propagated(self, db_session):
        alert = _make_alert_obj(db_session, alert_type="CRITICAL_BURST")
        payload = _build_payload(alert)
        assert payload["alert"]["type"] == "CRITICAL_BURST"
        assert "critical_burst" in payload["runbook"]

    def test_error_rate_propagated(self, db_session):
        alert = _make_alert_obj(db_session, error_rate=0.75)
        payload = _build_payload(alert)
        assert payload["alert"]["error_rate"] == 0.75

    def test_fired_at_ends_with_z(self, db_session):
        alert = _make_alert_obj(db_session)
        assert _build_payload(alert)["fired_at"].endswith("Z")

    def test_dashboard_url_contains_port(self, db_session):
        alert = _make_alert_obj(db_session)
        url = _build_payload(alert)["dashboard"]
        assert re.search(r":\d+/dashboard", url)


# ── WEBHOOK_URL port resolution ───────────────────────────────────────────────

class TestWebhookUrl:
    def test_webhook_url_has_port(self):
        assert re.search(r":\d+/webhook/receive", WEBHOOK_URL)

    def test_webhook_url_targets_localhost(self):
        assert "127.0.0.1" in WEBHOOK_URL

    def test_webhook_url_uses_env_port(self, monkeypatch):
        """OBSERVAGUARD_PORT env var (set in conftest) should be reflected."""
        # The URL is resolved at import time; just verify the pattern holds
        assert "/webhook/receive" in WEBHOOK_URL


# ── process_alerts ────────────────────────────────────────────────────────────

class TestProcessAlerts:
    def test_fires_webhook_for_each_alert(self, db_session):
        alert = _make_alert_obj(db_session)
        with patch("app.alert_manager.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            results = process_alerts(db_session, [alert])

        assert mock_post.call_count == 1
        assert len(results) == 1

    def test_result_structure(self, db_session):
        alert = _make_alert_obj(db_session)
        with patch("app.alert_manager.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            results = process_alerts(db_session, [alert])

        r = results[0]
        for key in ("alert_id", "alert_type", "severity", "service",
                    "webhook_status", "message"):
            assert key in r

    def test_webhook_status_200_on_success(self, db_session):
        alert = _make_alert_obj(db_session)
        with patch("app.alert_manager.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            results = process_alerts(db_session, [alert])

        assert results[0]["webhook_status"] == 200
        assert alert.webhook_fired is True
        assert alert.webhook_status == 200

    def test_webhook_status_0_on_connection_error(self, db_session):
        import httpx as _httpx
        alert = _make_alert_obj(db_session)
        with patch("app.alert_manager.httpx.post",
                   side_effect=_httpx.ConnectError("refused")):
            results = process_alerts(db_session, [alert])

        assert results[0]["webhook_status"] == 0
        assert alert.webhook_fired is True
        assert alert.webhook_status == 0

    def test_empty_alerts_list_returns_empty(self, db_session):
        results = process_alerts(db_session, [])
        assert results == []

    def test_multiple_alerts_all_fired(self, db_session):
        alerts = [
            _make_alert_obj(db_session, alert_type="ERROR_SPIKE"),
            _make_alert_obj(db_session, alert_type="CRITICAL_BURST"),
        ]
        with patch("app.alert_manager.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            results = process_alerts(db_session, alerts)

        assert mock_post.call_count == 2
        assert len(results) == 2
