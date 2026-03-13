from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import time

import pytest

from vibe.core.config import SessionLoggingConfig
from vibe.core.session.improve_prompt import (
    DEFAULT_IMPROVE_SESSION_LIMIT,
    build_improve_prompt,
)
from vibe.core.types import FunctionCall, LLMMessage, Role, ToolCall


def create_session(
    session_root: Path,
    session_id: str,
    *,
    user_prompt: str,
    assistant_text: str,
    cwd: str,
    bash_command: str | None = None,
    bash_failed: bool = False,
) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = session_root / f"test_{timestamp}_{session_id[:8]}"
    session_dir.mkdir(parents=True, exist_ok=True)

    messages = [LLMMessage(role=Role.user, content=user_prompt)]
    if bash_command is None:
        messages.append(LLMMessage(role=Role.assistant, content=assistant_text))
    else:
        tool_call = ToolCall(
            id=f"call_{session_id}",
            index=0,
            function=FunctionCall(
                name="bash",
                arguments=json.dumps({"command": bash_command}),
            ),
        )
        messages.extend(
            [
                LLMMessage(
                    role=Role.assistant,
                    content=assistant_text,
                    tool_calls=[tool_call],
                ),
                LLMMessage(
                    role=Role.tool,
                    name="bash",
                    tool_call_id=tool_call.id,
                    content=(
                        "<tool_error>bash failed: Command failed</tool_error>"
                        if bash_failed
                        else f"command: {bash_command}\nstdout: ok\nstderr: \nreturncode: 0"
                    ),
                ),
            ]
        )

    messages_path = session_dir / "messages.jsonl"
    with messages_path.open("w", encoding="utf-8") as f:
        for message in messages:
            f.write(json.dumps(message.model_dump(exclude_none=True)) + "\n")

    metadata = {
        "session_id": session_id,
        "start_time": "2026-03-10T12:00:00Z",
        "end_time": "2026-03-10T12:05:00Z",
        "title": user_prompt,
        "total_messages": len(messages),
        "stats": {
            "steps": 8 if bash_failed else 3,
            "session_prompt_tokens": 120,
            "session_completion_tokens": 60,
            "tool_calls_failed": 1 if bash_failed else 0,
            "tool_calls_rejected": 1 if bash_failed else 0,
        },
        "system_prompt": {"content": "System prompt", "role": "system"},
        "username": "testuser",
        "environment": {"working_directory": cwd},
        "git_commit": None,
        "git_branch": None,
    }
    (session_dir / "meta.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    return session_dir


@pytest.fixture
def session_config(tmp_path: Path) -> SessionLoggingConfig:
    session_root = tmp_path / "sessions"
    session_root.mkdir()
    return SessionLoggingConfig(
        save_dir=str(session_root),
        session_prefix="test",
        enabled=True,
    )


def test_build_improve_prompt_uses_recent_sessions_and_excludes_current(
    session_config: SessionLoggingConfig,
) -> None:
    session_root = Path(session_config.save_dir)
    current_session = create_session(
        session_root,
        "current-session",
        user_prompt="Current session should be excluded",
        assistant_text="I will inspect the command plumbing first.",
        cwd="/repo/current",
        bash_command="uv run pytest tests/cli/test_commands.py",
    )
    time.sleep(0.01)
    create_session(
        session_root,
        "failing-session",
        user_prompt="Investigate repeated CLI failures",
        assistant_text="I will inspect the command plumbing first.",
        cwd="/repo/a",
        bash_command="uv run pytest tests/cli/test_commands.py",
        bash_failed=True,
    )
    time.sleep(0.01)
    create_session(
        session_root,
        "recent-session",
        user_prompt="Implement a new slash command",
        assistant_text="I will inspect the command plumbing first.",
        cwd="/repo/b",
        bash_command="uv run pytest tests/session/test_session_loader.py",
    )

    prompt = build_improve_prompt(
        session_config=session_config,
        current_session_dir=current_session,
        limit=DEFAULT_IMPROVE_SESSION_LIMIT,
    )

    assert "Investigate repeated CLI failures" in prompt
    assert "Implement a new slash command" in prompt
    assert "Current session should be excluded" not in prompt
    assert "Repeated assistant phrases" in prompt
    assert "I will inspect the command plumbing first." in prompt
    assert "Most failed bash commands" in prompt
    assert "uv run pytest tests/cli/test_commands.py" in prompt
    assert "Friction to address" in prompt
    assert "Suggestions to try" in prompt
    assert "Repetition to remove" in prompt
    assert "Ship next" in prompt


def test_build_improve_prompt_requires_previous_sessions(
    session_config: SessionLoggingConfig,
) -> None:
    with pytest.raises(
        ValueError, match="No previous interactive sessions were found to analyze."
    ):
        build_improve_prompt(session_config=session_config)


def test_build_improve_prompt_ignores_legacy_improve_turns(
    session_config: SessionLoggingConfig,
) -> None:
    session_root = Path(session_config.save_dir)
    create_session(
        session_root,
        "legacy-improve",
        user_prompt=(
            "Analyze this digest of my last 2 interactive Mistral Vibe sessions "
            "and tell me how Vibe should improve.\n\n"
            "Respond with these sections:\n"
            "1. Friction to address\n"
            "2. Suggestions to try\n"
            "3. Repetition to remove\n"
            "4. Ship next\n"
        ),
        assistant_text="This is old /improve output that should be ignored.",
        cwd="/repo/legacy",
    )
    time.sleep(0.01)
    create_session(
        session_root,
        "real-session",
        user_prompt="Investigate slow test startup",
        assistant_text="I will inspect the startup path first.",
        cwd="/repo/real",
        bash_command="uv run pytest tests/session/test_session_logger.py",
    )

    prompt = build_improve_prompt(session_config=session_config)

    assert "Investigate slow test startup" in prompt
    assert "old /improve output" not in prompt
    assert "legacy-improve" not in prompt
