"""Tests for session cost formatting and JSON report export in session_exit.py."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from vibe.app_server import SessionExitSummary
from vibe.app_server.models import TokenUsage
from vibe.cli.session_exit import format_session_cost, print_session_resume_message


def test_format_session_cost_renders_correct_currency():
    summary = SessionExitSummary(
        session_id="test-12345",
        usage=TokenUsage(input_tokens=1000, output_tokens=200, total_tokens=1200),
        session_cost=0.0345,
    )
    assert format_session_cost(summary) == "Session cost: $0.0345"


def test_session_report_json_export(tmp_path: Path):
    report_file = tmp_path / "vibe_report.json"
    summary = SessionExitSummary(
        session_id="test-report-id",
        usage=TokenUsage(input_tokens=500, output_tokens=50, total_tokens=550),
        session_cost=0.0125,
    )

    with patch.dict("os.environ", {"VIBE_REPORT_PATH": str(report_file)}):
        print_session_resume_message(summary)

    assert report_file.exists()
    data = json.loads(report_file.read_text())
    assert data["session_id"] == "test-report-id"
    assert data["session_cost"] == 0.0125
    assert data["usage"]["total_tokens"] == 550
