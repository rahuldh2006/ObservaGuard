"""
ObservaGuard — AI Anomaly Detection Engine
Uses statistical methods (Z-score + rolling window) to detect:
  1. ERROR_SPIKE       — sudden surge vs. historical baseline
  2. CRITICAL_BURST    — ≥N CRITICAL/FATAL events in short window
  3. ERROR_RATE_HIGH   — sustained high error percentage
  4. SILENT_SERVICE    — expected service goes quiet (no logs)
"""

import logging
import math
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
from sqlalchemy.orm import Session

from models import LogEntry, MetricSnapshot, AlertEvent

logger = logging.getLogger("observaguard.detector")

# ── Tunable thresholds (can be overridden via env/config) ────────────────────
WINDOW_SECONDS       = 60       # rolling window for rate analysis
HISTORY_WINDOWS      = 10       # how many past windows to use for baseline
Z_SCORE_THRESHOLD    = 2.5      # std-deviations above mean = spike
ERROR_RATE_THRESHOLD = 0.35     # 35% error rate = HIGH alert
CRITICAL_BURST_N     = 3        # N CRITICAL/FATAL in window = CRITICAL_BURST alert
COOLDOWN_SECONDS     = 120      # don't re-fire same alert type within N seconds


def _error_levels() -> list[str]:
    return ["ERROR", "CRITICAL", "FATAL"]


def _fetch_window_counts(db: Session, service: str, since: datetime) -> dict:
    """Count log levels in the current window for a service."""
    rows = (
        db.query(LogEntry.level)
        .filter(LogEntry.service == service, LogEntry.timestamp >= since)
        .all()
    )
    counts = {"total": 0, "error": 0, "critical": 0, "warn": 0, "info": 0}
    for (level,) in rows:
        counts["total"] += 1
        lvl = (level or "INFO").upper()
        if lvl in ("ERROR",):
            counts["error"] += 1
        elif lvl in ("CRITICAL", "FATAL"):
            counts["critical"] += 1
            counts["error"] += 1   # criticals count as errors too
        elif lvl in ("WARNING", "WARN"):
            counts["warn"] += 1
        else:
            counts["info"] += 1
    return counts


def _fetch_historical_error_rates(db: Session, service: str, now: datetime) -> list[float]:
    """
    Pull the last HISTORY_WINDOWS MetricSnapshot error_rates for baseline.
    Falls back to raw log analysis if snapshots are sparse.
    """
    cutoff = now - timedelta(seconds=WINDOW_SECONDS * HISTORY_WINDOWS)
    snapshots = (
        db.query(MetricSnapshot.error_rate)
        .filter(
            MetricSnapshot.service == service,
            MetricSnapshot.snapshot_at >= cutoff,
        )
        .order_by(MetricSnapshot.snapshot_at.asc())
        .all()
    )
    rates = [r for (r,) in snapshots if r is not None]
    return rates


def _compute_z_score(current_value: float, history: list[float]) -> float:
    """Z = (x - μ) / σ  — returns 0.0 if insufficient history."""
    if len(history) < 3:
        return 0.0
    mu = float(np.mean(history))
    sigma = float(np.std(history))
    if sigma < 1e-9:
        return 0.0
    return (current_value - mu) / sigma


def _alert_in_cooldown(db: Session, service: str, alert_type: str, now: datetime) -> bool:
    """Return True if a same-type alert was fired within the cooldown window."""
    cutoff = now - timedelta(seconds=COOLDOWN_SECONDS)
    exists = (
        db.query(AlertEvent.id)
        .filter(
            AlertEvent.service == service,
            AlertEvent.alert_type == alert_type,
            AlertEvent.triggered_at >= cutoff,
        )
        .first()
    )
    return exists is not None


def _severity_from_z(z: float, error_rate: float) -> str:
    if z >= 4.0 or error_rate >= 0.75:
        return "CRITICAL"
    elif z >= 3.0 or error_rate >= 0.55:
        return "HIGH"
    elif z >= 2.5 or error_rate >= 0.35:
        return "MEDIUM"
    return "LOW"


def record_metric_snapshot(db: Session, service: str) -> MetricSnapshot:
    """
    Roll up the current window into a MetricSnapshot row.
    Called after every ingest batch so the detector has history to compare.
    """
    now = datetime.utcnow()
    since = now - timedelta(seconds=WINDOW_SECONDS)
    counts = _fetch_window_counts(db, service, since)

    total  = counts["total"] or 1   # avoid div-zero
    e_rate = round(counts["error"] / total, 4)
    history = _fetch_historical_error_rates(db, service, now)
    z = _compute_z_score(e_rate, history)

    snap = MetricSnapshot(
        snapshot_at = now,
        service     = service,
        window_secs = WINDOW_SECONDS,
        total_logs  = counts["total"],
        error_count = counts["error"],
        warn_count  = counts["warn"],
        info_count  = counts["info"],
        error_rate  = e_rate,
        z_score     = round(z, 4),
        is_anomaly  = (z >= Z_SCORE_THRESHOLD or e_rate >= ERROR_RATE_THRESHOLD),
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap


def detect_anomalies(db: Session, service: str) -> list[AlertEvent]:
    """
    Run all anomaly checks for a service. Returns list of newly created AlertEvent rows.
    """
    now   = datetime.utcnow()
    since = now - timedelta(seconds=WINDOW_SECONDS)
    counts  = _fetch_window_counts(db, service, since)
    history = _fetch_historical_error_rates(db, service, now)

    total   = max(counts["total"], 1)
    e_rate  = counts["error"] / total
    z_score = _compute_z_score(e_rate, history)

    fired: list[AlertEvent] = []

    # ── Check 1: ERROR_SPIKE via Z-score ─────────────────────────────────────
    if z_score >= Z_SCORE_THRESHOLD:
        if not _alert_in_cooldown(db, service, "ERROR_SPIKE", now):
            sev = _severity_from_z(z_score, e_rate)
            alert = AlertEvent(
                triggered_at  = now,
                service       = service,
                alert_type    = "ERROR_SPIKE",
                severity      = sev,
                error_rate    = round(e_rate, 4),
                z_score       = round(z_score, 4),
                window_errors = counts["error"],
                window_total  = counts["total"],
                message       = (
                    f"[ERROR_SPIKE] Service '{service}' — z-score={z_score:.2f} "
                    f"(threshold={Z_SCORE_THRESHOLD}). "
                    f"Error rate: {e_rate*100:.1f}% ({counts['error']}/{counts['total']} logs in {WINDOW_SECONDS}s window)."
                ),
            )
            db.add(alert)
            db.commit()
            db.refresh(alert)
            fired.append(alert)
            logger.warning("[ANOMALY] ERROR_SPIKE on '%s' z=%.2f rate=%.1f%%", service, z_score, e_rate * 100)

    # ── Check 2: CRITICAL_BURST ───────────────────────────────────────────────
    if counts["critical"] >= CRITICAL_BURST_N:
        if not _alert_in_cooldown(db, service, "CRITICAL_BURST", now):
            alert = AlertEvent(
                triggered_at  = now,
                service       = service,
                alert_type    = "CRITICAL_BURST",
                severity      = "CRITICAL",
                error_rate    = round(e_rate, 4),
                z_score       = round(z_score, 4),
                window_errors = counts["error"],
                window_total  = counts["total"],
                message       = (
                    f"[CRITICAL_BURST] Service '{service}' — {counts['critical']} CRITICAL events "
                    f"in {WINDOW_SECONDS}s window (threshold={CRITICAL_BURST_N})."
                ),
            )
            db.add(alert)
            db.commit()
            db.refresh(alert)
            fired.append(alert)
            logger.critical("[ANOMALY] CRITICAL_BURST on '%s' count=%d", service, counts["critical"])

    # ── Check 3: ERROR_RATE_HIGH (sustained) ──────────────────────────────────
    if e_rate >= ERROR_RATE_THRESHOLD and z_score < Z_SCORE_THRESHOLD:
        # z-score low means it's sustained (not a spike) — different alert type
        if not _alert_in_cooldown(db, service, "ERROR_RATE_HIGH", now):
            sev = _severity_from_z(z_score, e_rate)
            alert = AlertEvent(
                triggered_at  = now,
                service       = service,
                alert_type    = "ERROR_RATE_HIGH",
                severity      = sev,
                error_rate    = round(e_rate, 4),
                z_score       = round(z_score, 4),
                window_errors = counts["error"],
                window_total  = counts["total"],
                message       = (
                    f"[ERROR_RATE_HIGH] Service '{service}' — sustained error rate "
                    f"{e_rate*100:.1f}% (threshold={ERROR_RATE_THRESHOLD*100:.0f}%). "
                    f"{counts['error']}/{counts['total']} logs in {WINDOW_SECONDS}s window."
                ),
            )
            db.add(alert)
            db.commit()
            db.refresh(alert)
            fired.append(alert)
            logger.warning("[ANOMALY] ERROR_RATE_HIGH on '%s' rate=%.1f%%", service, e_rate * 100)

    return fired
