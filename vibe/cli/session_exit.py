from __future__ import annotations

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


def print_session_resume_message(summary: SessionExitSummary | None) -> None:
    if summary is None or summary.session_id is None:
        return

    print()
    print(format_session_usage(summary.usage))
    print()
    rprint("To continue this session, run: [bold dark_orange]vibe --continue[/]")
    session_id = shorten_session_id(summary.session_id)
    rprint(f"Or: [bold dark_orange]vibe --resume {session_id}[/]")
