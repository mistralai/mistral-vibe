from __future__ import annotations

import pytest

from vibe.cli.textual_ui.session_exit import print_session_resume_message
from vibe.core.types import AgentStats


def test_print_session_resume_message_skips_output_without_session_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    print_session_resume_message(None, AgentStats())

    assert capsys.readouterr().out == ""


def test_print_session_resume_message_prints_resume_commands_and_usage(
    capsys: pytest.CaptureFixture[str],
) -> None:
    print_session_resume_message(
        "12345678-1234-1234-1234-123456789abc",
        AgentStats(session_prompt_tokens=14_867, session_completion_tokens=6),
    )

    assert capsys.readouterr().out == (
        "\n"
        "Total tokens used this session: input=14,867 output=6 (total=14,873)\n"
        "\n"
        "To continue this session, run: vibe --continue\n"
        "Or: vibe --resume 12345678-1234-1234-1234-123456789abc\n"
    )


def test_print_session_resume_message_prints_zero_usage_for_resumed_run_without_llm_activity(
    capsys: pytest.CaptureFixture[str],
) -> None:
    print_session_resume_message("12345678", AgentStats())

    assert capsys.readouterr().out == (
        "\n"
        "Total tokens used this session: input=0 output=0 (total=0)\n"
        "\n"
        "To continue this session, run: vibe --continue\n"
        "Or: vibe --resume 12345678\n"
    )
