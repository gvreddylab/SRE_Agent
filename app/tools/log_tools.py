"""
Log analysis helpers.

Pattern-based extraction of signals from raw container logs so the LLM
receives a pre-filtered, high-signal summary rather than hundreds of raw
log lines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Pattern


# ──────────────────────────────────────────────────────────────
# Signal patterns
# ──────────────────────────────────────────────────────────────

_ERROR_PATTERNS: list[Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\b(error|err)\b",
        r"\b(exception|traceback|panic|fatal|critical)\b",
        r"\b(oom|out.?of.?memory|killed)\b",
        r"\b(connection.?refused|timeout|timed.?out)\b",
        r"\b(failed|failure|crash|segfault|core.?dump)\b",
        r"\b(permission.?denied|unauthori[zs]ed|forbidden)\b",
        r"\b(disk.?full|no.?space.?left)\b",
        r"exit.?code.?[^0]\d*",
        r"signal.?(KILL|TERM|SEGV|ABRT)",
        r"java\.lang\.\w+Exception",
        r"Traceback \(most recent call last\)",
    ]
]

_IGNORE_PATTERNS: list[Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"^\s*$",
        r"health.?check",
        r"liveness.?probe",
        r"/ping|/health|/ready",
    ]
]


@dataclass
class LogAnalysis:
    total_lines: int
    error_lines: list[str]
    error_count: int
    signals: list[str]
    truncated: bool
    summary: str


def extract_error_lines(raw_logs: str, max_errors: int = 30) -> list[str]:
    """Return lines matching error patterns, skipping health-check noise."""
    errors: list[str] = []
    for line in raw_logs.splitlines():
        if any(ig.search(line) for ig in _IGNORE_PATTERNS):
            continue
        if any(p.search(line) for p in _ERROR_PATTERNS):
            errors.append(line.strip())
            if len(errors) >= max_errors:
                break
    return errors


def extract_signals(raw_logs: str) -> list[str]:
    """
    Return unique, deduplicated, human-readable signal phrases found
    in the logs (e.g. "OOM Killed", "Connection Refused", "Panic").
    """
    signal_map = {
        "OOM / Memory Kill": re.compile(r"oom|out.?of.?memory|killed", re.I),
        "Connection Refused": re.compile(r"connection.?refused", re.I),
        "Timeout": re.compile(r"time.?out|timed.?out", re.I),
        "Panic / Crash": re.compile(r"panic|crash|fatal|segfault", re.I),
        "Exception": re.compile(r"exception|traceback", re.I),
        "Permission Denied": re.compile(r"permission.?denied|forbidden|unauthori[zs]ed", re.I),
        "Disk Full": re.compile(r"disk.?full|no.?space.?left", re.I),
        "Exit Non-Zero": re.compile(r"exit.?code.?[^0]\d*", re.I),
        "Java Exception": re.compile(r"java\.lang\.\w+Exception", re.I),
    }
    found: list[str] = []
    for signal_name, pattern in signal_map.items():
        if pattern.search(raw_logs):
            found.append(signal_name)
    return found


def analyse_logs(raw_logs: str, max_chars: int = 8000) -> LogAnalysis:
    """
    Produce a structured analysis of raw container logs.

    Args:
        raw_logs:  The full string from kubectl logs.
        max_chars: Truncate input beyond this limit before analysis.

    Returns:
        LogAnalysis with error lines, detected signals, and a one-paragraph summary.
    """
    truncated = len(raw_logs) > max_chars
    working_logs = raw_logs[:max_chars] if truncated else raw_logs
    lines = working_logs.splitlines()
    error_lines = extract_error_lines(working_logs)
    signals = extract_signals(working_logs)

    parts: list[str] = []
    if signals:
        parts.append(f"Detected signals: {', '.join(signals)}.")
    if error_lines:
        parts.append(f"Found {len(error_lines)} error line(s). First: {error_lines[0][:200]}")
    if truncated:
        parts.append(f"(Logs truncated to {max_chars} chars; original was {len(raw_logs)} chars.)")
    if not signals and not error_lines:
        parts.append("No obvious error signals detected in the log sample.")

    return LogAnalysis(
        total_lines=len(lines),
        error_lines=error_lines,
        error_count=len(error_lines),
        signals=signals,
        truncated=truncated,
        summary=" ".join(parts),
    )


def format_log_context(analysis: LogAnalysis, max_error_lines: int = 20) -> str:
    """Produce a compact, LLM-ready log context string."""
    parts: list[str] = [
        f"Log Analysis ({analysis.total_lines} total lines):",
        f"  Signals: {', '.join(analysis.signals) or 'none'}",
        f"  Error count: {analysis.error_count}",
    ]
    if analysis.truncated:
        parts.append("  [Logs were truncated]")

    if analysis.error_lines:
        parts.append("--- Key Error Lines ---")
        for line in analysis.error_lines[:max_error_lines]:
            parts.append(f"  {line}")
        if len(analysis.error_lines) > max_error_lines:
            parts.append(f"  ... and {len(analysis.error_lines) - max_error_lines} more")

    return "\n".join(parts)
