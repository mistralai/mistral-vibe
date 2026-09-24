from __future__ import annotations

import json
import os
from pathlib import Path

from rich import print as rprint

from vibe.app_server import SessionExitSummary
from vibe.app_server.models import TokenUsage
from vibe.utils.session_id import shorten_session_id


def format_session_usage(usage: TokenUsage) -> str:
    return (
        "Total tokens used this session: "
        f"input={usage.input_tokens:,} "
        f"output={usage.output_tokens:,} "
        f"(total={usage.total_tokens:,})"
    )


def format_session_cost(summary: SessionExitSummary) -> str:
    return f"Session cost: ${getattr(summary, 'session_cost', 0.0):.4f}"


def _export_session_report(summary: SessionExitSummary) -> None:
    report_path = os.environ.get("VIBE_REPORT_PATH")
    if not report_path:
        return
    try:
        data = {
            "session_id": summary.session_id,
            "session_cost": getattr(summary, "session_cost", 0.0),
            "usage": {
                "input_tokens": summary.usage.input_tokens,
                "output_tokens": summary.usage.output_tokens,
                "total_tokens": summary.usage.total_tokens,
            },
        }
        Path(report_path).write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def print_session_resume_message(summary: SessionExitSummary | None) -> None:
    if summary is None or summary.session_id is None:
        return

    _export_session_report(summary)

    print()
    print(format_session_usage(summary.usage))
    if getattr(summary, "session_cost", 0.0) > 0:
        print(format_session_cost(summary))
    print()
    rprint("To continue this session, run: [bold dark_orange]vibe --continue[/]")
    session_id = shorten_session_id(summary.session_id)
    rprint(f"Or: [bold dark_orange]vibe --resume {session_id}[/]")
