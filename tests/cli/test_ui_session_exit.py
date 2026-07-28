from __future__ import annotations

import pytest

from vibe.app_server import SessionExitSummary
from vibe.app_server.models import TokenUsage
from vibe.cli.session_exit import print_session_resume_message


def test_print_session_resume_message_skips_output_without_session_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    print_session_resume_message(None)

    assert capsys.readouterr().out == ""


def test_print_session_resume_message_prints_resume_commands_and_usage(
    capsys: pytest.CaptureFixture[str],
) -> None:
    print_session_resume_message(
        SessionExitSummary(
            session_id="12345678-1234-1234-1234-123456789abc",
            usage=TokenUsage(input_tokens=14_867, output_tokens=6, total_tokens=14_873),
        )
    )

    assert capsys.readouterr().out == (
        "\n"
        "Total tokens used this session: input=14,867 output=6 (total=14,873)\n"
        "\n"
        "To continue this session, run: vibe --continue\n"
        "Or: vibe --resume 12345678\n"
    )


def test_print_session_resume_message_prints_zero_usage_for_resumed_run_without_llm_activity(
    capsys: pytest.CaptureFixture[str],
) -> None:
    print_session_resume_message(
        SessionExitSummary(session_id="12345678", usage=TokenUsage())
    )

    assert capsys.readouterr().out == (
        "\n"
        "Total tokens used this session: input=0 output=0 (total=0)\n"
        "\n"
        "To continue this session, run: vibe --continue\n"
        "Or: vibe --resume 12345678\n"
    )
