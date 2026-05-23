"""
ObservaGuard — Main FastAPI Application
API-first Intelligent Observability & Event Watchdog

Endpoints:
  POST /ingest          — ingest raw or JSON log lines
  POST /ingest/bulk     — ingest multiple log lines at once
  GET  /health          — service health summary
  GET  /metrics         — time-series metric snapshots
  GET  /alerts          — alert history
  GET  /dashboard       — HTML dashboard
  POST /webhook/receive — simulated webhook receiver (mock target)
  GET  /services        — list known services
"""

import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func, desc

from database import get_db, init_db
from models import LogEntry, AlertEvent, MetricSnapshot
from log_parser import parse_log_line, parse_bulk
from anomaly_detector import detect_anomalies, record_metric_snapshot
from alert_manager import process_alerts

# ── Logging config ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("observaguard.main")


# ── Startup / shutdown ────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("ObservaGuard started — SQLite ready.")
    yield
    logger.info("ObservaGuard shutting down.")


app = FastAPI(
    title="ObservaGuard",
    description="Intelligent Observability & Event Watchdog — SRE Edition",
    version="1.0.0",
    lifespan=lifespan,
)

templates = Jinja2Templates(directory="templates")


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class LogIngestRequest(BaseModel):
    log_line: str = Field(..., description="Raw log line to parse and ingest")
    service:  str = Field("default", description="Service name override")


class BulkIngestRequest(BaseModel):
    lines:   list[str] = Field(..., description="List of raw log lines")
    service: str       = Field("default", description="Default service name")


class WebhookPayload(BaseModel):
    event:   str
    version: str
    fired_at: str
    alert:   dict


# ── Helper: run detection pipeline ───────────────────────────────────────────

def _run_detection_pipeline(db: Session, service: str) -> dict:
    """Record snapshot → detect anomalies → fire webhooks. Returns summary."""
    snap   = record_metric_snapshot(db, service)
    alerts = detect_anomalies(db, service)
    webhook_results = process_alerts(db, alerts) if alerts else []
    return {
        "snapshot":        {
            "error_rate": snap.error_rate,
            "z_score":    snap.z_score,
            "is_anomaly": snap.is_anomaly,
            "total_logs": snap.total_logs,
        },
        "alerts_fired":    len(alerts),
        "webhook_results": webhook_results,
    }


# ── Routes: Log Ingestion ─────────────────────────────────────────────────────

@app.post("/ingest", summary="Ingest a single log line")
def ingest_log(req: LogIngestRequest, db: Session = Depends(get_db)):
    parsed = parse_log_line(req.log_line, default_service=req.service)
    if not parsed:
        raise HTTPException(status_code=400, detail="Could not parse log line.")

    entry = LogEntry(**{k: v for k, v in parsed.items()})
    db.add(entry)
    db.commit()
    db.refresh(entry)

    detection = _run_detection_pipeline(db, parsed["service"])

    return {
        "status":    "ingested",
        "entry_id":  entry.id,
        "parsed":    {
            "timestamp": parsed["timestamp"].isoformat(),
            "level":     parsed["level"],
            "service":   parsed["service"],
            "message":   parsed["message"][:120],
        },
        "detection": detection,
    }


@app.post("/ingest/bulk", summary="Ingest multiple log lines at once")
def ingest_bulk(req: BulkIngestRequest, db: Session = Depends(get_db)):
    parsed_list = parse_bulk(req.lines, default_service=req.service)
    if not parsed_list:
        raise HTTPException(status_code=400, detail="No parseable log lines found.")

    services_seen = set()
    for parsed in parsed_list:
        entry = LogEntry(**{k: v for k, v in parsed.items()})
        db.add(entry)
        services_seen.add(parsed["service"])
    db.commit()

    detection_summary = {}
    for svc in services_seen:
        detection_summary[svc] = _run_detection_pipeline(db, svc)

    return {
        "status":           "ingested",
        "lines_parsed":     len(parsed_list),
        "lines_submitted":  len(req.lines),
        "services":         list(services_seen),
        "detection":        detection_summary,
    }


# ── Routes: Health & Metrics ──────────────────────────────────────────────────

@app.get("/health", summary="Current health summary per service")
def get_health(db: Session = Depends(get_db)):
    since = datetime.utcnow() - timedelta(minutes=5)

    # Latest snapshot per service
    subq = (
        db.query(
            MetricSnapshot.service,
            func.max(MetricSnapshot.snapshot_at).label("latest")
        )
        .group_by(MetricSnapshot.service)
        .subquery()
    )
    snaps = (
        db.query(MetricSnapshot)
        .join(subq, (MetricSnapshot.service == subq.c.service) &
                    (MetricSnapshot.snapshot_at == subq.c.latest))
        .all()
    )

    services = []
    for s in snaps:
        age_secs = (datetime.utcnow() - s.snapshot_at).total_seconds()
        if s.error_rate >= 0.55 or s.is_anomaly:
            status = "DEGRADED"
        elif s.error_rate >= 0.20:
            status = "WARNING"
        else:
            status = "HEALTHY"

        services.append({
            "service":     s.service,
            "status":      status,
            "error_rate":  round(s.error_rate * 100, 1),
            "z_score":     s.z_score,
            "is_anomaly":  s.is_anomaly,
            "total_logs":  s.total_logs,
            "snapshot_age_secs": round(age_secs, 1),
            "snapshot_at": s.snapshot_at.isoformat(),
        })

    # Active alerts in last 10 min
    alert_cutoff = datetime.utcnow() - timedelta(minutes=10)
    active_alerts = db.query(func.count(AlertEvent.id)).filter(
        AlertEvent.triggered_at >= alert_cutoff
    ).scalar()

    overall = "HEALTHY"
    if any(s["status"] == "DEGRADED" for s in services):
        overall = "DEGRADED"
    elif any(s["status"] == "WARNING" for s in services):
        overall = "WARNING"

    return {
        "overall_status": overall,
        "checked_at":     datetime.utcnow().isoformat(),
        "active_alerts":  active_alerts,
        "services":       services,
    }


@app.get("/metrics", summary="Time-series metric snapshots")
def get_metrics(
    service:  Optional[str] = None,
    minutes:  int = 60,
    db: Session = Depends(get_db),
):
    cutoff = datetime.utcnow() - timedelta(minutes=minutes)
    q = db.query(MetricSnapshot).filter(MetricSnapshot.snapshot_at >= cutoff)
    if service:
        q = q.filter(MetricSnapshot.service == service)
    snaps = q.order_by(MetricSnapshot.snapshot_at.asc()).all()

    return {
        "count":   len(snaps),
        "service": service or "all",
        "minutes": minutes,
        "data": [
            {
                "t":           s.snapshot_at.isoformat(),
                "service":     s.service,
                "error_rate":  round(s.error_rate * 100, 2),
                "z_score":     s.z_score,
                "total_logs":  s.total_logs,
                "error_count": s.error_count,
                "is_anomaly":  s.is_anomaly,
            }
            for s in snaps
        ],
    }


@app.get("/alerts", summary="Alert event history")
def get_alerts(
    limit:   int = 50,
    service: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(AlertEvent).order_by(desc(AlertEvent.triggered_at))
    if service:
        q = q.filter(AlertEvent.service == service)
    alerts = q.limit(limit).all()

    return {
        "count": len(alerts),
        "alerts": [
            {
                "id":            a.id,
                "triggered_at":  a.triggered_at.isoformat(),
                "service":       a.service,
                "alert_type":    a.alert_type,
                "severity":      a.severity,
                "error_rate":    round((a.error_rate or 0) * 100, 1),
                "z_score":       a.z_score,
                "window_errors": a.window_errors,
                "window_total":  a.window_total,
                "message":       a.message,
                "webhook_fired": a.webhook_fired,
                "webhook_status":a.webhook_status,
            }
            for a in alerts
        ],
    }


@app.get("/services", summary="List all known services")
def get_services(db: Session = Depends(get_db)):
    rows = db.query(LogEntry.service).distinct().all()
    return {"services": [r[0] for r in rows]}


# ── Routes: Webhook Mock Receiver ─────────────────────────────────────────────

_received_webhooks: list[dict] = []   # in-memory store for demo


@app.post("/webhook/receive", summary="Simulated webhook receiver")
async def webhook_receive(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}

    entry = {
        "received_at": datetime.utcnow().isoformat(),
        "headers":     dict(request.headers),
        "payload":     body,
    }
    _received_webhooks.append(entry)
    logger.info(
        "[WEBHOOK RECEIVED] type=%s service=%s severity=%s",
        body.get("alert", {}).get("type", "?"),
        body.get("alert", {}).get("service", "?"),
        body.get("alert", {}).get("severity", "?"),
    )
    return {"status": "received", "webhook_count": len(_received_webhooks)}


@app.get("/webhook/history", summary="All received webhooks (in-memory)")
def webhook_history():
    return {"count": len(_received_webhooks), "webhooks": _received_webhooks[-20:]}


# ── Routes: Dashboard ─────────────────────────────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse, summary="Live HTML Dashboard")
def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/", response_class=HTMLResponse)
def root():
    return HTMLResponse(
        '<meta http-equiv="refresh" content="0;url=/dashboard">'
        "<p>Redirecting to <a href='/dashboard'>dashboard</a>...</p>"
    )
