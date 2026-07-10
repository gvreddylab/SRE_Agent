"""Unit tests for log analysis tools — no external dependencies required."""

import pytest
from app.tools.log_tools import analyse_logs, extract_error_lines, extract_signals


CRASH_LOG = """
2024-01-15T10:00:01Z Starting application...
2024-01-15T10:00:02Z Connecting to database...
2024-01-15T10:00:03Z ERROR: Connection refused to postgres:5432
2024-01-15T10:00:03Z Traceback (most recent call last):
2024-01-15T10:00:03Z   File "app.py", line 42, in connect
2024-01-15T10:00:03Z     raise ConnectionError("database connection refused")
2024-01-15T10:00:03Z ConnectionError: database connection refused
2024-01-15T10:00:04Z Fatal: application cannot start
"""

OOM_LOG = """
2024-01-15T10:00:01Z Allocating memory...
2024-01-15T10:00:02Z Allocated 100 MB
2024-01-15T10:00:03Z Allocated 200 MB
Killed
"""

CLEAN_LOG = """
2024-01-15T10:00:01Z Server started on port 8080
2024-01-15T10:00:02Z GET /health 200 OK
2024-01-15T10:00:03Z Serving request
"""


def test_extract_error_lines_crash():
    errors = extract_error_lines(CRASH_LOG)
    assert len(errors) > 0
    assert any("Connection refused" in e or "connection refused" in e for e in errors)


def test_extract_error_lines_clean():
    errors = extract_error_lines(CLEAN_LOG)
    assert len(errors) == 0


def test_extract_signals_oom():
    signals = extract_signals(OOM_LOG)
    assert "OOM / Memory Kill" in signals


def test_extract_signals_connection():
    signals = extract_signals(CRASH_LOG)
    assert "Connection Refused" in signals


def test_extract_signals_clean():
    signals = extract_signals(CLEAN_LOG)
    assert len(signals) == 0


def test_analyse_logs_crash():
    result = analyse_logs(CRASH_LOG)
    assert result.error_count > 0
    assert "Connection Refused" in result.signals
    assert "Connection Refused" in result.summary


def test_analyse_logs_truncation():
    big_log = "x" * 10_000
    result = analyse_logs(big_log, max_chars=1000)
    assert result.truncated is True


def test_analyse_logs_clean():
    result = analyse_logs(CLEAN_LOG)
    assert result.error_count == 0
    assert "No obvious error" in result.summary
