"""
Unit tests for app.log_parser — covers all supported log formats,
level normalisation, bulk parsing, and edge cases.
"""

import json
from datetime import datetime

import pytest

from app.log_parser import parse_log_line, parse_bulk


# ── Bracketed timestamp format ────────────────────────────────────────────────

class TestBracketedFormat:
    def test_parses_error_with_service(self):
        line = "[2024-01-15 12:34:56] ERROR api-gateway: connection timeout"
        result = parse_log_line(line)
        assert result is not None
        assert result["level"] == "ERROR"
        assert result["service"] == "api-gateway"
        assert "timeout" in result["message"]

    def test_parses_info(self):
        line = "[2024-01-15 08:00:00] INFO auth-service: user 42 authenticated"
        result = parse_log_line(line)
        assert result["level"] == "INFO"
        assert result["service"] == "auth-service"

    def test_parses_critical(self):
        line = "[2024-01-15 23:59:59] CRITICAL payments: DB unreachable"
        result = parse_log_line(line)
        assert result["level"] == "CRITICAL"

    def test_preserves_raw_line(self):
        line = "[2024-01-15 12:00:00] DEBUG svc: hello"
        result = parse_log_line(line)
        assert result["raw"] == line

    def test_timestamp_parsed(self):
        line = "[2024-06-01 10:30:00] INFO svc: ok"
        result = parse_log_line(line)
        assert isinstance(result["timestamp"], datetime)
        assert result["timestamp"].year == 2024


# ── JSON format ───────────────────────────────────────────────────────────────

class TestJsonFormat:
    def test_standard_json_log(self):
        payload = {"timestamp": "2024-01-15T12:34:56", "level": "WARNING",
                   "service": "order-processor", "message": "slow query"}
        result = parse_log_line(json.dumps(payload))
        assert result["level"] == "WARNING"
        assert result["service"] == "order-processor"
        assert result["message"] == "slow query"

    def test_json_with_severity_key(self):
        payload = {"ts": "2024-01-15T12:00:00", "severity": "ERROR",
                   "app": "inventory", "msg": "disk full"}
        result = parse_log_line(json.dumps(payload))
        assert result["level"] == "ERROR"
        assert result["service"] == "inventory"

    def test_json_missing_service_uses_default(self):
        payload = {"level": "INFO", "message": "startup"}
        result = parse_log_line(json.dumps(payload), default_service="fallback-svc")
        assert result["service"] == "fallback-svc"

    def test_malformed_json_falls_through_to_other_patterns(self):
        # Starts with '{' but is not valid JSON — should not raise
        result = parse_log_line("{this is not json}")
        assert result is not None   # falls through to fallback


# ── Plain level-first format ──────────────────────────────────────────────────

class TestPlainFormat:
    def test_error_plain(self):
        result = parse_log_line("ERROR something went very wrong")
        assert result["level"] == "ERROR"
        assert result["message"] == "something went very wrong"

    def test_info_plain(self):
        result = parse_log_line("INFO server started on port 8080")
        assert result["level"] == "INFO"

    def test_plain_uses_default_service(self):
        result = parse_log_line("WARNING high memory", default_service="my-svc")
        assert result["service"] == "my-svc"


# ── Level normalisation ───────────────────────────────────────────────────────

class TestLevelNormalisation:
    def test_warn_becomes_warning(self):
        result = parse_log_line("[2024-01-15 12:00:00] WARN svc: slow")
        assert result["level"] == "WARNING"

    def test_fatal_becomes_critical(self):
        result = parse_log_line("[2024-01-15 12:00:00] FATAL svc: crashed")
        assert result["level"] == "CRITICAL"

    def test_debug_unchanged(self):
        result = parse_log_line("[2024-01-15 12:00:00] DEBUG svc: trace")
        assert result["level"] == "DEBUG"

    def test_case_insensitive(self):
        result = parse_log_line("[2024-01-15 12:00:00] error svc: oops")
        assert result["level"] == "ERROR"


# ── Blank and fallback lines ──────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_string_returns_none(self):
        assert parse_log_line("") is None

    def test_whitespace_only_returns_none(self):
        assert parse_log_line("   \t\n  ") is None

    def test_unrecognised_format_falls_back_to_info(self):
        result = parse_log_line("completely unstructured log line with no level")
        assert result is not None
        assert result["level"] == "INFO"
        assert result["message"] == "completely unstructured log line with no level"

    def test_result_always_has_required_keys(self):
        required = {"timestamp", "level", "service", "message", "raw"}
        result = parse_log_line("[2024-01-15 12:00:00] INFO svc: ok")
        assert required.issubset(result.keys())


# ── Bulk parsing ──────────────────────────────────────────────────────────────

class TestParseBulk:
    def test_skips_blank_lines(self):
        lines = [
            "[2024-01-15 12:00:00] INFO svc: start",
            "",
            "   ",
            "[2024-01-15 12:00:01] ERROR svc: fail",
        ]
        results = parse_bulk(lines)
        assert len(results) == 2

    def test_all_blank_returns_empty(self):
        assert parse_bulk(["", "  ", "\n"]) == []

    def test_preserves_order(self):
        lines = [
            "[2024-01-15 12:00:00] INFO svc: first",
            "[2024-01-15 12:00:01] ERROR svc: second",
            "[2024-01-15 12:00:02] WARNING svc: third",
        ]
        results = parse_bulk(lines)
        assert results[0]["level"] == "INFO"
        assert results[1]["level"] == "ERROR"
        assert results[2]["level"] == "WARNING"

    def test_default_service_applied(self):
        lines = ["ERROR something bad"]
        results = parse_bulk(lines, default_service="test-service")
        assert results[0]["service"] == "test-service"

    def test_empty_list_returns_empty(self):
        assert parse_bulk([]) == []
