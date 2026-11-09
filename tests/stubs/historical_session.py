"""On-disk fixtures matching sessions written by released legacy Vibe versions."""

from __future__ import annotations

import json
from pathlib import Path

from vibe.core.config import VibeConfigSchema
from vibe.core.types import LLMMessage


def write_historical_session(
    *,
    config: VibeConfigSchema,
    session_id: str,
    messages: list[LLMMessage],
    cwd: Path | None = None,
    parent_session_id: str | None = None,
    active_model: str | None = None,
) -> Path:
    session_dir = Path(config.session_logging.save_dir) / (
        f"{config.session_logging.session_prefix}_20260101_120000_{session_id[:8]}"
    )
    session_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "session_id": session_id,
        "parent_session_id": parent_session_id,
        "start_time": "2026-01-01T12:00:00Z",
        "end_time": "2026-01-01T12:05:00Z",
        "git_commit": None,
        "git_branch": None,
        "username": "test-user",
        "environment": {"working_directory": str(cwd or Path.cwd())},
        "config": {"active_model": active_model} if active_model else {},
    }
    (session_dir / "meta.json").write_text(json.dumps(metadata), encoding="utf-8")
    (session_dir / "messages.jsonl").write_text(
        "".join(message.model_dump_json() + "\n" for message in messages),
        encoding="utf-8",
    )
    return session_dir
