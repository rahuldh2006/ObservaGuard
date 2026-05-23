"""
ObservaGuard — Log Parser
Accepts raw log strings and structured JSON log payloads.
Supports common log formats: syslog-style, JSON, and Apache/Nginx-style.
"""

import re
import json
import logging
from datetime import datetime
from typing import Optional

from dateutil import parser as dateparser

logger = logging.getLogger("observaguard.parser")

# ── Regex patterns for common log formats ────────────────────────────────────

# Pattern 1: [2024-01-15 12:34:56] ERROR service_name: message
BRACKETED_TS = re.compile(
    r'^\[(?P<ts>[^\]]+)\]\s+(?P<level>DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)'
    r'(?:\s+(?P<service>[^\s:]+))?[:\s]+(?P<msg>.+)$',
    re.IGNORECASE,
)

# Pattern 2: 2024-01-15T12:34:56Z level=ERROR service=api msg="..."
KV_FORMAT = re.compile(
    r'(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[^\s]*)'
    r'.*?level[=: ]+(?P<level>DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)'
    r'(?:.*?service[=: ]+(?P<service>\S+))?'
    r'(?:.*?msg[=: ]+"?(?P<msg>[^"]+)"?)?',
    re.IGNORECASE,
)

# Pattern 3: Jan 15 12:34:56 hostname process[pid]: level message
SYSLOG = re.compile(
    r'^(?P<ts>\w{3}\s+\d+\s+\d{2}:\d{2}:\d{2})\s+\S+\s+(?P<service>\S+?)(?:\[\d+\])?:\s+'
    r'(?P<level>DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)?\s*(?P<msg>.+)$',
    re.IGNORECASE,
)

# Pattern 4: plain  "ERROR some message"  (minimal)
PLAIN = re.compile(
    r'^(?P<level>DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)\s+(?P<msg>.+)$',
    re.IGNORECASE,
)

LEVEL_MAP = {
    "WARN": "WARNING",
    "FATAL": "CRITICAL",
}


def _normalize_level(raw: str) -> str:
    up = raw.upper()
    return LEVEL_MAP.get(up, up)


def _safe_parse_ts(ts_str: str) -> datetime:
    try:
        return dateparser.parse(ts_str, fuzzy=True)
    except Exception:
        return datetime.utcnow()


def parse_log_line(raw: str, default_service: str = "default") -> Optional[dict]:
    """
    Parse a single raw log line into a structured dict.
    Returns None if the line is blank or un-parseable.
    """
    line = raw.strip()
    if not line:
        return None

    # ── Attempt JSON first ───────────────────────────────────────────────────
    if line.startswith("{"):
        try:
            data = json.loads(line)
            ts_raw = (
                data.get("timestamp") or data.get("time") or
                data.get("ts") or data.get("@timestamp")
            )
            return {
                "timestamp": _safe_parse_ts(str(ts_raw)) if ts_raw else datetime.utcnow(),
                "level":     _normalize_level(str(data.get("level", data.get("severity", "INFO")))),
                "service":   str(data.get("service", data.get("app", default_service))),
                "message":   str(data.get("message", data.get("msg", data.get("event", line)))),
                "raw":       raw,
            }
        except json.JSONDecodeError:
            pass

    # ── Bracketed timestamp format ───────────────────────────────────────────
    m = BRACKETED_TS.match(line)
    if m:
        return {
            "timestamp": _safe_parse_ts(m.group("ts")),
            "level":     _normalize_level(m.group("level")),
            "service":   m.group("service") or default_service,
            "message":   m.group("msg").strip(),
            "raw":       raw,
        }

    # ── Key-value format ─────────────────────────────────────────────────────
    m = KV_FORMAT.match(line)
    if m:
        return {
            "timestamp": _safe_parse_ts(m.group("ts")),
            "level":     _normalize_level(m.group("level")),
            "service":   m.group("service") or default_service,
            "message":   (m.group("msg") or line).strip(),
            "raw":       raw,
        }

    # ── Syslog format ────────────────────────────────────────────────────────
    m = SYSLOG.match(line)
    if m:
        return {
            "timestamp": _safe_parse_ts(m.group("ts")),
            "level":     _normalize_level(m.group("level") or "INFO"),
            "service":   m.group("service") or default_service,
            "message":   m.group("msg").strip(),
            "raw":       raw,
        }

    # ── Plain level-first format ─────────────────────────────────────────────
    m = PLAIN.match(line)
    if m:
        return {
            "timestamp": datetime.utcnow(),
            "level":     _normalize_level(m.group("level")),
            "service":   default_service,
            "message":   m.group("msg").strip(),
            "raw":       raw,
        }

    # ── Fallback: store as INFO ──────────────────────────────────────────────
    logger.debug("Unrecognized format, storing as INFO: %s", line[:80])
    return {
        "timestamp": datetime.utcnow(),
        "level":     "INFO",
        "service":   default_service,
        "message":   line,
        "raw":       raw,
    }


def parse_bulk(lines: list[str], default_service: str = "default") -> list[dict]:
    """Parse a list of raw log lines, skipping blanks."""
    results = []
    for line in lines:
        parsed = parse_log_line(line, default_service)
        if parsed:
            results.append(parsed)
    return results
