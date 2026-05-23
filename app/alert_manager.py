"""
ObservaGuard — Webhook Alert Manager
Fires simulated webhook POSTs when anomalies are detected.
The webhook target is a local mock endpoint (/webhook/receive) running
on the same FastAPI server — no external service required.

The port is read from the OBSERVAGUARD_PORT environment variable so it
automatically matches whatever port the server was started on.
"""

import logging
import os

import httpx
from sqlalchemy.orm import Session

from .models import AlertEvent

logger = logging.getLogger("observaguard.alerts")

# ── Webhook target — resolves port from environment ───────────────────────────
_PORT = os.environ.get("OBSERVAGUARD_PORT", "8080")
WEBHOOK_URL     = f"http://127.0.0.1:{_PORT}/webhook/receive"
WEBHOOK_TIMEOUT = 5.0   # seconds

# Severity → emoji for log output
SEV_ICON = {
    "LOW":      "🟡",
    "MEDIUM":   "🟠",
    "HIGH":     "🔴",
    "CRITICAL": "🚨",
}


def _build_payload(alert: AlertEvent) -> dict:
    """Build the webhook JSON payload from an AlertEvent."""
    return {
        "event":    "observaguard.alert",
        "version":  "1.0",
        "fired_at": alert.triggered_at.isoformat() + "Z",
        "alert": {
            "id":           alert.id,
            "type":         alert.alert_type,
            "severity":     alert.severity,
            "service":      alert.service,
            "error_rate":   alert.error_rate,
            "z_score":      alert.z_score,
            "window_errors":alert.window_errors,
            "window_total": alert.window_total,
            "message":      alert.message,
        },
        "runbook":   f"https://wiki.example.com/runbooks/{alert.alert_type.lower()}",
        "dashboard": f"http://127.0.0.1:{_PORT}/dashboard",
    }


def fire_webhook(db: Session, alert: AlertEvent) -> int:
    """
    POST the alert payload to the configured webhook URL.
    Updates alert.webhook_fired and alert.webhook_status in the DB.
    Returns the HTTP status code (or 0 on connection error).
    """
    payload = _build_payload(alert)
    icon    = SEV_ICON.get(alert.severity, "⚠️")

    logger.info(
        "%s WEBHOOK → %s | %s | service=%s | rate=%.1f%% | z=%.2f",
        icon, alert.alert_type, alert.severity,
        alert.service, (alert.error_rate or 0) * 100, alert.z_score or 0,
    )

    status_code = 0
    try:
        resp = httpx.post(
            WEBHOOK_URL,
            json=payload,
            timeout=WEBHOOK_TIMEOUT,
            headers={"Content-Type": "application/json", "X-ObservaGuard-Event": alert.alert_type},
        )
        status_code = resp.status_code
        logger.info("Webhook response: HTTP %d", status_code)
    except httpx.ConnectError:
        logger.warning("Webhook target unreachable — recorded locally only.")
        status_code = 0
    except Exception as exc:
        logger.error("Webhook fire failed: %s", exc)
        status_code = -1

    # ── Persist result ────────────────────────────────────────────────────────
    alert.webhook_fired  = True
    alert.webhook_status = status_code
    db.commit()

    return status_code


def process_alerts(db: Session, alerts: list[AlertEvent]) -> list[dict]:
    """Fire webhooks for a list of newly detected alerts. Returns summary dicts."""
    results = []
    for alert in alerts:
        status = fire_webhook(db, alert)
        results.append({
            "alert_id":       alert.id,
            "alert_type":     alert.alert_type,
            "severity":       alert.severity,
            "service":        alert.service,
            "webhook_status": status,
            "message":        alert.message,
        })
    return results
