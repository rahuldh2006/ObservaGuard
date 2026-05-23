"""
ObservaGuard — SQLAlchemy ORM Models
Tables: LogEntry, AlertEvent, MetricSnapshot
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Text
from .database import Base


class LogEntry(Base):
    """A single parsed log line."""
    __tablename__ = "log_entries"

    id          = Column(Integer, primary_key=True, index=True)
    timestamp   = Column(DateTime, default=datetime.utcnow, index=True)
    service     = Column(String(64), index=True, default="default")
    level       = Column(String(16), index=True)   # DEBUG, INFO, WARNING, ERROR, CRITICAL
    message     = Column(Text)
    raw         = Column(Text)                      # original log line
    ingested_at = Column(DateTime, default=datetime.utcnow)


class AlertEvent(Base):
    """A triggered anomaly alert."""
    __tablename__ = "alert_events"

    id              = Column(Integer, primary_key=True, index=True)
    triggered_at    = Column(DateTime, default=datetime.utcnow, index=True)
    service         = Column(String(64), index=True)
    alert_type      = Column(String(64))   # ERROR_SPIKE, CRITICAL_BURST, ERROR_RATE_HIGH
    severity        = Column(String(16))   # LOW, MEDIUM, HIGH, CRITICAL
    error_rate      = Column(Float)        # error % at time of alert
    z_score         = Column(Float)        # statistical z-score
    window_errors   = Column(Integer)      # errors in detection window
    window_total    = Column(Integer)      # total logs in detection window
    message         = Column(Text)
    webhook_fired   = Column(Boolean, default=False)
    webhook_status  = Column(Integer, nullable=True)   # HTTP status of webhook call


class MetricSnapshot(Base):
    """Periodic metric rollup for trend visualization."""
    __tablename__ = "metric_snapshots"

    id           = Column(Integer, primary_key=True, index=True)
    snapshot_at  = Column(DateTime, default=datetime.utcnow, index=True)
    service      = Column(String(64), index=True)
    window_secs  = Column(Integer, default=60)
    total_logs   = Column(Integer, default=0)
    error_count  = Column(Integer, default=0)
    warn_count   = Column(Integer, default=0)
    info_count   = Column(Integer, default=0)
    error_rate   = Column(Float, default=0.0)   # 0.0 – 1.0
    z_score      = Column(Float, default=0.0)
    is_anomaly   = Column(Boolean, default=False)
