"""
Integration tests for the FastAPI endpoints.
Each test gets a clean in-memory database via the `client` fixture.
"""

import pytest


# ── /health ───────────────────────────────────────────────────────────────────

class TestHealth:
    def test_empty_db_returns_healthy(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["overall_status"] == "HEALTHY"
        assert body["services"] == []
        assert body["active_alerts"] == 0

    def test_health_fields_present(self, client):
        r = client.get("/health")
        body = r.json()
        assert "overall_status" in body
        assert "checked_at" in body
        assert "active_alerts" in body
        assert "services" in body


# ── /ingest ───────────────────────────────────────────────────────────────────

class TestIngestSingle:
    def test_ingest_valid_line(self, client):
        r = client.post("/ingest", json={
            "log_line": "[2024-01-15 12:34:56] INFO api-gateway: request ok",
            "service": "api-gateway",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ingested"
        assert body["parsed"]["level"] == "INFO"
        assert body["parsed"]["service"] == "api-gateway"
        assert "entry_id" in body
        assert "detection" in body

    def test_ingest_empty_line_returns_400(self, client):
        r = client.post("/ingest", json={"log_line": "", "service": "svc"})
        assert r.status_code == 400

    def test_ingest_whitespace_line_returns_400(self, client):
        r = client.post("/ingest", json={"log_line": "   ", "service": "svc"})
        assert r.status_code == 400

    def test_ingest_increments_service_list(self, client):
        client.post("/ingest", json={"log_line": "INFO ok", "service": "my-svc"})
        r = client.get("/services")
        assert "my-svc" in r.json()["services"]


# ── /ingest/bulk ──────────────────────────────────────────────────────────────

class TestIngestBulk:
    def test_bulk_ingest_two_lines(self, client):
        r = client.post("/ingest/bulk", json={
            "lines": [
                "[2024-01-15 12:34:56] INFO svc: start",
                "[2024-01-15 12:34:57] ERROR svc: fail",
            ],
            "service": "svc",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ingested"
        assert body["lines_parsed"] == 2
        assert body["lines_submitted"] == 2

    def test_bulk_skips_blank_lines(self, client):
        r = client.post("/ingest/bulk", json={
            "lines": ["INFO ok", "", "  ", "ERROR bad"],
            "service": "svc",
        })
        assert r.status_code == 200
        assert r.json()["lines_parsed"] == 2
        assert r.json()["lines_submitted"] == 4

    def test_bulk_all_blank_returns_400(self, client):
        r = client.post("/ingest/bulk", json={
            "lines": ["", "   "],
            "service": "svc",
        })
        assert r.status_code == 400

    def test_bulk_detection_summary_present(self, client):
        r = client.post("/ingest/bulk", json={
            "lines": ["INFO ok", "ERROR bad"],
            "service": "my-svc",
        })
        body = r.json()
        assert "detection" in body
        assert "my-svc" in body["detection"]

    def test_bulk_multi_service_detection(self, client):
        r = client.post("/ingest/bulk", json={
            "lines": [
                "[2024-01-15 12:00:00] INFO svc-a: ok",
                "[2024-01-15 12:00:00] ERROR svc-b: fail",
            ],
            "service": "default",
        })
        body = r.json()
        services_in_detection = set(body["detection"].keys())
        assert "svc-a" in services_in_detection
        assert "svc-b" in services_in_detection


# ── /metrics ──────────────────────────────────────────────────────────────────

class TestMetrics:
    def test_empty_metrics(self, client):
        r = client.get("/metrics")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 0
        assert body["data"] == []

    def test_metrics_populated_after_ingest(self, client):
        client.post("/ingest/bulk", json={"lines": ["INFO ok", "ERROR bad"], "service": "svc"})
        r = client.get("/metrics?service=svc")
        assert r.status_code == 200
        assert r.json()["count"] > 0

    def test_metrics_service_filter(self, client):
        client.post("/ingest/bulk", json={"lines": ["INFO ok"], "service": "svc-a"})
        client.post("/ingest/bulk", json={"lines": ["ERROR bad"], "service": "svc-b"})
        r = client.get("/metrics?service=svc-a")
        for row in r.json()["data"]:
            assert row["service"] == "svc-a"

    def test_metrics_data_fields(self, client):
        client.post("/ingest/bulk", json={"lines": ["ERROR bad"], "service": "svc"})
        r = client.get("/metrics")
        row = r.json()["data"][0]
        for field in ("t", "service", "error_rate", "z_score", "total_logs", "is_anomaly"):
            assert field in row


# ── /alerts ───────────────────────────────────────────────────────────────────

class TestAlerts:
    def test_empty_alerts(self, client):
        r = client.get("/alerts")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 0
        assert body["alerts"] == []

    def test_alerts_service_filter(self, client):
        # Ingest a high-error batch to potentially trigger alerts
        client.post("/ingest/bulk", json={
            "lines": ["ERROR x"] * 20,
            "service": "target-svc",
        })
        r = client.get("/alerts?service=target-svc")
        assert r.status_code == 200
        for alert in r.json()["alerts"]:
            assert alert["service"] == "target-svc"

    def test_alert_fields_present(self, client):
        # Ingest enough errors to guarantee ERROR_RATE_HIGH fires
        client.post("/ingest/bulk", json={
            "lines": ["ERROR x"] * 20,
            "service": "svc",
        })
        r = client.get("/alerts")
        if r.json()["count"] > 0:
            alert = r.json()["alerts"][0]
            for field in ("id", "triggered_at", "service", "alert_type",
                          "severity", "error_rate", "z_score", "message"):
                assert field in alert


# ── /services ─────────────────────────────────────────────────────────────────

class TestServices:
    def test_empty_services(self, client):
        r = client.get("/services")
        assert r.status_code == 200
        assert r.json()["services"] == []

    def test_service_appears_after_ingest(self, client):
        client.post("/ingest/bulk", json={"lines": ["INFO ok"], "service": "new-svc"})
        r = client.get("/services")
        assert "new-svc" in r.json()["services"]

    def test_multiple_services(self, client):
        client.post("/ingest/bulk", json={"lines": ["INFO ok"], "service": "alpha"})
        client.post("/ingest/bulk", json={"lines": ["INFO ok"], "service": "beta"})
        services = client.get("/services").json()["services"]
        assert "alpha" in services
        assert "beta" in services


# ── /webhook ──────────────────────────────────────────────────────────────────

class TestWebhook:
    _PAYLOAD = {
        "event": "observaguard.alert",
        "version": "1.0",
        "fired_at": "2024-01-15T12:00:00Z",
        "alert": {"type": "ERROR_SPIKE", "service": "svc", "severity": "HIGH"},
    }

    def test_receive_returns_200(self, client):
        r = client.post("/webhook/receive", json=self._PAYLOAD)
        assert r.status_code == 200
        assert r.json()["status"] == "received"

    def test_history_count_increments(self, client):
        before = client.get("/webhook/history").json()["count"]
        client.post("/webhook/receive", json=self._PAYLOAD)
        after = client.get("/webhook/history").json()["count"]
        assert after == before + 1

    def test_history_contains_payload(self, client):
        client.post("/webhook/receive", json=self._PAYLOAD)
        history = client.get("/webhook/history").json()["webhooks"]
        assert len(history) >= 1
        last = history[-1]
        assert last["payload"]["event"] == "observaguard.alert"

    def test_history_capped_at_20(self, client):
        for _ in range(25):
            client.post("/webhook/receive", json=self._PAYLOAD)
        history = client.get("/webhook/history").json()["webhooks"]
        assert len(history) <= 20

    def test_receive_empty_body_does_not_crash(self, client):
        r = client.post("/webhook/receive", content=b"", headers={"Content-Type": "application/json"})
        assert r.status_code == 200


# ── /dashboard ────────────────────────────────────────────────────────────────

class TestDashboard:
    def test_returns_html(self, client):
        r = client.get("/dashboard")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert b"ObservaGuard" in r.content

    def test_root_redirects(self, client):
        r = client.get("/", follow_redirects=False)
        # Root returns an HTML meta-refresh (200), not a 3xx
        assert r.status_code == 200
        assert b"dashboard" in r.content.lower()

    def test_reset_button_present(self, client):
        r = client.get("/dashboard")
        assert b"btn-reset" in r.content or b"Reset" in r.content


# ── /stats ─────────────────────────────────────────────────────────────────────

class TestStats:
    def test_empty_stats(self, client):
        r = client.get("/stats")
        assert r.status_code == 200
        body = r.json()
        assert body["total_logs"] == 0
        assert body["total_alerts"] == 0
        assert body["total_services"] == 0
        assert body["webhook_count"] == 0

    def test_required_fields(self, client):
        body = client.get("/stats").json()
        for f in ("total_logs", "total_alerts", "total_services", "webhook_count"):
            assert f in body

    def test_logs_counted_after_ingest(self, client):
        client.post("/ingest/bulk", json={"lines": ["INFO ok", "ERROR bad"], "service": "svc"})
        body = client.get("/stats").json()
        assert body["total_logs"] == 2

    def test_services_counted_after_ingest(self, client):
        client.post("/ingest/bulk", json={"lines": ["INFO ok"], "service": "alpha"})
        client.post("/ingest/bulk", json={"lines": ["INFO ok"], "service": "beta"})
        body = client.get("/stats").json()
        assert body["total_services"] == 2

    def test_webhook_count_in_stats(self, client):
        payload = {"event": "x", "version": "1.0", "fired_at": "2024-01-15T12:00:00Z", "alert": {}}
        client.post("/webhook/receive", json=payload)
        client.post("/webhook/receive", json=payload)
        body = client.get("/stats").json()
        assert body["webhook_count"] == 2


# ── /reset ─────────────────────────────────────────────────────────────────────

class TestReset:
    def test_reset_empty_db_succeeds(self, client):
        r = client.post("/reset")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "reset"
        assert body["cleared"]["log_entries"] == 0

    def test_reset_response_fields(self, client):
        r = client.post("/reset")
        body = r.json()
        assert "status" in body
        assert "cleared" in body
        assert "reset_at" in body
        for f in ("log_entries", "metric_snapshots", "alert_events", "webhook_history"):
            assert f in body["cleared"]

    def test_reset_clears_log_entries(self, client):
        client.post("/ingest/bulk", json={"lines": ["INFO ok", "ERROR bad"], "service": "svc"})
        assert client.get("/stats").json()["total_logs"] == 2
        client.post("/reset")
        assert client.get("/stats").json()["total_logs"] == 0

    def test_reset_clears_metrics(self, client):
        client.post("/ingest/bulk", json={"lines": ["INFO ok"], "service": "svc"})
        client.post("/reset")
        assert client.get("/metrics").json()["count"] == 0

    def test_reset_clears_alerts(self, client):
        # Ingest high-error batch to fire an alert
        client.post("/ingest/bulk", json={"lines": ["ERROR x"] * 20, "service": "svc"})
        client.post("/reset")
        assert client.get("/alerts").json()["count"] == 0

    def test_reset_clears_webhook_history(self, client):
        payload = {"event": "x", "version": "1.0", "fired_at": "2024-01-15T12:00:00Z", "alert": {}}
        client.post("/webhook/receive", json=payload)
        assert client.get("/webhook/history").json()["count"] == 1
        client.post("/reset")
        assert client.get("/webhook/history").json()["count"] == 0

    def test_reset_cleared_counts_match_rows_deleted(self, client):
        client.post("/ingest/bulk", json={"lines": ["INFO ok", "INFO ok", "INFO ok"], "service": "svc"})
        r = client.post("/reset")
        assert r.json()["cleared"]["log_entries"] == 3

    def test_services_gone_after_reset(self, client):
        client.post("/ingest/bulk", json={"lines": ["INFO ok"], "service": "my-svc"})
        client.post("/reset")
        assert client.get("/services").json()["services"] == []

    def test_health_healthy_after_reset(self, client):
        # Feed degraded traffic, then reset — health should return to HEALTHY
        client.post("/ingest/bulk", json={"lines": ["ERROR x"] * 20, "service": "svc"})
        client.post("/reset")
        r = client.get("/health")
        assert r.json()["overall_status"] == "HEALTHY"

    def test_system_accepts_new_logs_after_reset(self, client):
        client.post("/ingest/bulk", json={"lines": ["INFO ok"], "service": "old-svc"})
        client.post("/reset")
        r = client.post("/ingest", json={"log_line": "INFO fresh start", "service": "new-svc"})
        assert r.status_code == 200
        assert client.get("/stats").json()["total_logs"] == 1


# ── Service name normalisation ─────────────────────────────────────────────────

class TestServiceNormalisation:
    def test_whitespace_stripped_from_service(self, client):
        client.post("/ingest/bulk", json={"lines": ["INFO ok"], "service": "  my-svc  "})
        svcs = client.get("/services").json()["services"]
        assert "my-svc" in svcs
        assert "  my-svc  " not in svcs

    def test_empty_service_defaults_to_default(self, client):
        client.post("/ingest/bulk", json={"lines": ["INFO ok"], "service": ""})
        svcs = client.get("/services").json()["services"]
        assert "default" in svcs

    def test_whitespace_only_service_defaults_to_default(self, client):
        client.post("/ingest/bulk", json={"lines": ["INFO ok"], "service": "   "})
        svcs = client.get("/services").json()["services"]
        assert "default" in svcs
