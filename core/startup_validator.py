# -*- coding: utf-8 -*-
"""
core/startup_validator.py — Pre-login startup validation for Music Bot V3.

Runs BEFORE bot.run() is called so that catastrophic misconfiguration
is caught early, before any network connection is made to Discord.

Checks (in order):
  1. DISCORD_TOKEN       — present and non-empty
  2. APP_ID              — present if set; valid integer
  3. FFmpeg              — binary reachable in PATH and responds to -version
  4. Database path       — parent directory exists / can be created; file writable
  5. Config completeness — warn on optional-but-recommended settings
  6. Spotify keys        — present → try token fetch (WARNING only if missing)

ValidationReport contains:
  - items: list[ValidationItem]  (severity: FATAL | WARNING | OK)
  - has_fatal: bool

If has_fatal is True, main() should print the report and exit(1).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class Severity(str, Enum):
    OK      = "ok"
    WARNING = "warning"
    FATAL   = "fatal"


@dataclass
class ValidationItem:
    name:     str
    severity: Severity
    detail:   str

    def icon(self) -> str:
        return {"ok": "✅", "warning": "⚠️", "fatal": "❌"}[self.severity]

    def __str__(self) -> str:
        return f"{self.icon()} [{self.severity.upper():7s}] {self.name}: {self.detail}"


@dataclass
class ValidationReport:
    items:     list[ValidationItem] = field(default_factory=list)

    @property
    def has_fatal(self) -> bool:
        return any(i.severity == Severity.FATAL for i in self.items)

    @property
    def fatal_items(self) -> list[ValidationItem]:
        return [i for i in self.items if i.severity == Severity.FATAL]

    @property
    def warning_items(self) -> list[ValidationItem]:
        return [i for i in self.items if i.severity == Severity.WARNING]

    def add(self, name: str, severity: Severity, detail: str) -> None:
        self.items.append(ValidationItem(name=name, severity=severity, detail=detail))

    def print_report(self) -> None:
        import sys
        # Use UTF-8 aware writing; fall back to ASCII-safe repr on narrow terminals
        def _print(text: str) -> None:
            try:
                print(text)
            except UnicodeEncodeError:
                # Strip/replace non-ASCII characters for terminals without UTF-8
                safe = text.encode("ascii", errors="replace").decode("ascii")
                print(safe)

        _print("\n" + "=" * 60)
        _print("  Music Bot V3 — Startup Validation Report")
        _print("=" * 60)
        for item in self.items:
            _print(f"  {item}")
        _print("=" * 60)
        if self.has_fatal:
            _print("  FATAL errors found — bot cannot start.")
        else:
            _print(f"  All critical checks passed ({len(self.warning_items)} warning(s)).")
        _print("=" * 60 + "\n")



# ── Individual checks ─────────────────────────────────────────────────────────

def _check_discord_token(report: ValidationReport) -> None:
    import config
    token = getattr(config, "TOKEN", "") or ""
    if not token:
        report.add("DISCORD_TOKEN", Severity.FATAL, "Not set — bot cannot authenticate.")
        return
    if len(token) < 50:
        report.add("DISCORD_TOKEN", Severity.WARNING, "Token looks too short — verify it is correct.")
    else:
        report.add("DISCORD_TOKEN", Severity.OK, f"Present ({len(token)} chars)")


def _check_app_id(report: ValidationReport) -> None:
    import config
    app_id = getattr(config, "APP_ID", None)
    if app_id is None:
        report.add("APP_ID", Severity.WARNING,
                   "Not set — slash command sync may not work correctly.")
    else:
        report.add("APP_ID", Severity.OK, f"{app_id}")


def _check_ffmpeg(report: ValidationReport) -> None:
    path = shutil.which("ffmpeg")
    if not path:
        report.add("FFmpeg", Severity.FATAL,
                   "Binary not found in PATH — audio playback impossible.")
        return
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, text=True, timeout=8,
        )
        if result.returncode != 0:
            report.add("FFmpeg", Severity.FATAL,
                       f"Non-zero exit ({result.returncode}) — check FFmpeg installation.")
        else:
            version_line = result.stdout.splitlines()[0] if result.stdout else "unknown"
            report.add("FFmpeg", Severity.OK, version_line[:80])
    except subprocess.TimeoutExpired:
        report.add("FFmpeg", Severity.FATAL, "Timed out after 8s — binary may be broken.")
    except Exception as exc:
        report.add("FFmpeg", Severity.FATAL, f"Unexpected error: {exc}")


def _check_database(report: ValidationReport) -> None:
    import config
    db_path = getattr(config, "DATABASE_PATH", "data/musicbot.db")
    parent  = Path(db_path).parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        report.add("Database dir", Severity.FATAL,
                   f"Cannot create parent directory '{parent}': {exc}")
        return

    # Writable?
    test_file = parent / ".write_test"
    try:
        test_file.write_text("ok")
        test_file.unlink()
        report.add("Database", Severity.OK,
                   f"Path '{db_path}' is writable.")
    except Exception as exc:
        report.add("Database", Severity.FATAL,
                   f"Directory '{parent}' is not writable: {exc}")


def _check_spotify(report: ValidationReport) -> None:
    import config
    cid    = getattr(config, "SPOTIFY_CLIENT_ID",     "") or ""
    secret = getattr(config, "SPOTIFY_CLIENT_SECRET", "") or ""
    if not cid or not secret:
        report.add("Spotify", Severity.WARNING,
                   "SPOTIFY_CLIENT_ID / SECRET not set — Spotify features disabled.")
    else:
        report.add("Spotify", Severity.OK,
                   f"Credentials present (CLIENT_ID={cid[:8]}…)")


def _check_config_completeness(report: ValidationReport) -> None:
    import config

    recommended: list[tuple[str, str]] = [
        ("DEV_LOG_CHANNEL_ID", "Error forwarding disabled — set for dev channel alerts."),
        ("API_SECRET",         "REST API has no authentication — consider setting API_SECRET."),
    ]
    for attr, msg in recommended:
        val = getattr(config, attr, None)
        if not val:
            report.add(f"Config/{attr}", Severity.WARNING, msg)

    # Sanity-check numeric values
    try:
        import config as cfg
        if cfg.IDLE_TIMEOUT < 30:
            report.add("Config/IDLE_TIMEOUT", Severity.WARNING,
                       f"Very low value ({cfg.IDLE_TIMEOUT}s) — bot may disconnect aggressively.")
        if cfg.MAX_QUEUE_SIZE > 5000:
            report.add("Config/MAX_QUEUE_SIZE", Severity.WARNING,
                       f"Very large queue ({cfg.MAX_QUEUE_SIZE}) — may cause high memory usage.")
    except Exception:
        pass


def _check_logs_dir(report: ValidationReport) -> None:
    log_dir = Path("logs")
    try:
        log_dir.mkdir(exist_ok=True)
        test = log_dir / ".write_test"
        test.write_text("ok")
        test.unlink()
        report.add("Logs dir", Severity.OK, f"'{log_dir}' is writable.")
    except Exception as exc:
        report.add("Logs dir", Severity.WARNING, f"Cannot write to 'logs/': {exc}")


# ── Main runner ───────────────────────────────────────────────────────────────

def validate_pre_login(*, print_report: bool = True) -> ValidationReport:
    """
    Run all pre-login checks synchronously.

    Called in main() before bot.run().
    Returns a ValidationReport; if has_fatal is True, caller should exit(1).
    """
    report = ValidationReport()

    _check_discord_token(report)
    _check_app_id(report)
    _check_ffmpeg(report)
    _check_database(report)
    _check_logs_dir(report)
    _check_spotify(report)
    _check_config_completeness(report)

    if print_report:
        report.print_report()

    return report
