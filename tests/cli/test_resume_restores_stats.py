from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from vibe.cli.cli import _resume_previous_session
from vibe.core.session.session_loader import SessionLoader
from vibe.core.types import LLMMessage, Role


def _write_session(session_dir: Path) -> Path:
    session_dir.mkdir(parents=True)
    messages = [
        LLMMessage(role=Role.user, content="Hello"),
        LLMMessage(role=Role.assistant, content="Hi there!"),
    ]
    with (session_dir / "messages.jsonl").open("w", encoding="utf-8") as f:
        for message in messages:
            f.write(json.dumps(message.model_dump(exclude_none=True)) + "\n")
    (session_dir / "meta.json").write_text(
        json.dumps({
            "session_id": "11111111-1111-1111-1111-111111111111",
            "start_time": "2026-01-01T12:00:00Z",
            "end_time": "2026-01-01T12:05:00Z",
            "total_messages": 2,
            "stats": {
                "steps": 3,
                "session_prompt_tokens": 1000,
                "session_completion_tokens": 200,
                "context_tokens": 42_000,
            },
            "username": "testuser",
            "environment": {"working_directory": "/test"},
        }),
        encoding="utf-8",
    )
    return session_dir


def test_resume_previous_session_restores_stats(tmp_path: Path) -> None:
    agent_loop = build_test_agent_loop(config=build_test_vibe_config())
    session_path = _write_session(tmp_path / "session")
    loaded_messages, _ = SessionLoader.load_session(session_path)

    _resume_previous_session(agent_loop, loaded_messages, session_path)

    assert agent_loop.stats.context_tokens == 42_000
    assert agent_loop.stats.session_prompt_tokens == 1000
    assert agent_loop.stats.session_completion_tokens == 200
    assert agent_loop.stats.steps == 3


def test_resume_previous_session_tolerates_missing_stats(tmp_path: Path) -> None:
    agent_loop = build_test_agent_loop(config=build_test_vibe_config())
    session_path = _write_session(tmp_path / "session")
    metadata_path = session_path / "meta.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    del metadata["stats"]
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    loaded_messages, _ = SessionLoader.load_session(session_path)

    _resume_previous_session(agent_loop, loaded_messages, session_path)

    assert agent_loop.stats.context_tokens == 0
