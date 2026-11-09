from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest

from mistralai_vibe_local_harness.vibe._host import UnifiedHarnessSessionBackendHost
from mistralai_vibe_local_harness.vibe._storage import UnifiedSessionStore
from vibe.app_server._unified_import_writer import UnifiedSessionImportWriter
from vibe.core.config import SessionLoggingConfig
from vibe.core.imports.adapters.claude_session import (
    ClaudeSessionSource,
    ClaudeSessionSummary,
)
from vibe.core.types import Role


@pytest.mark.asyncio
async def test_imports_a_claude_transcript_into_a_native_unified_session(
    tmp_path: Path,
) -> None:
    """*Prepare*: A Claude transcript containing private thinking and tool history.
    *Do*: Import it twice and resume the resulting Unified session.
    *Assert*: Import is idempotent and thinking is stored outside public history.
    """
    # Prepare
    source_id = UUID("11111111-1111-1111-1111-111111111111")
    source_path = tmp_path / "claude" / f"{source_id}.jsonl"
    source_path.parent.mkdir()
    rows = [
        {
            "type": "user",
            "cwd": "/workspace",
            "timestamp": "2026-01-01T10:00:00Z",
            "message": {"role": "user", "content": "inspect"},
        },
        {
            "type": "assistant",
            "cwd": "/workspace",
            "timestamp": "2026-01-01T10:00:01Z",
            "message": {"role": "assistant", "content": ""},
        },
        {
            "type": "assistant",
            "cwd": "/workspace",
            "timestamp": "2026-01-01T10:00:01Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "private reasoning",
                        "signature": "signature-1",
                    },
                    {
                        "type": "tool_use",
                        "id": "call-1",
                        "name": "Bash",
                        "input": {"command": "pwd"},
                    },
                ],
            },
        },
        {
            "type": "user",
            "cwd": "/workspace",
            "timestamp": "2026-01-01T10:00:02Z",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "call-1", "content": "ok"}
                ],
            },
        },
        {"type": "ai-title", "aiTitle": "Imported session"},
    ]
    source_path.write_text("".join(f"{json.dumps(row)}\n" for row in rows))

    source = ClaudeSessionSource(source_path.parent)
    transcript = source.read(ClaudeSessionSummary(id=source_id, path=source_path))
    translated = source.translate(transcript)
    assert translated.source_harness == "claude"
    assert translated.source_session_id == str(source_id)
    assert translated.source_cwd is None
    writer = UnifiedSessionImportWriter(
        SessionLoggingConfig(enabled=True, save_dir=str(tmp_path / "vibe"))
    )

    # Do
    await writer.write(translated)
    await writer.write(translated)

    stores = [
        path for path in (tmp_path / "vibe" / "unified").iterdir() if path.is_dir()
    ]
    assert len(stores) == 1
    stored = UnifiedSessionStore(tmp_path / "vibe", stores[0].name).load()
    assert stored.runtime_state.import_provenance is None
    external_import = stored.runtime_state.session_metadata.external_import
    assert external_import is not None
    assert external_import.harness == "claude"
    assert external_import.session_id == str(source_id)
    assert stored.projection_state.snapshot.session.title == "Imported session"
    assert "private reasoning" not in stored.projection_state.model_dump_json()
    assert stored.interop_export is not None
    assert "private reasoning" in stored.interop_export.model_dump_json()
    assert not list((tmp_path / "vibe").glob("session_*"))

    host = UnifiedHarnessSessionBackendHost(tmp_path / "vibe")
    resumed = await host.resume(stores[0].name, history_limit=10)

    # Assert
    assert resumed.session_id == stores[0].name
    await resumed.shutdown()
    await host.shutdown()


def test_translates_claude_content_without_exposing_thinking(tmp_path: Path) -> None:
    """*Prepare*: Claude content with text, thinking, and tool blocks.
    *Do*: Translate the transcript.
    *Assert*: Thinking is opaque while visible text and tool history are retained.
    """
    # Prepare
    source_id = UUID("11111111-1111-1111-1111-111111111111")
    source_path = tmp_path / f"{source_id}.jsonl"
    rows = [
        {
            "type": "assistant",
            "cwd": "/workspace",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "private reasoning",
                        "signature": "signature-1",
                    },
                    {"type": "text", "text": "visible answer"},
                    {
                        "type": "tool_use",
                        "id": "call-1",
                        "name": "Bash",
                        "input": {"command": "pwd"},
                    },
                ],
            },
        },
        {
            "type": "user",
            "cwd": "/workspace",
            "message": {
                "role": "user",
                "content": [
                    {"type": "text", "text": "before"},
                    {"type": "tool_result", "tool_use_id": "call-1", "content": "ok"},
                    {"type": "text", "text": "after"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "ignored-in-v0",
                        },
                    },
                ],
            },
        },
    ]
    source_path.write_text("".join(f"{json.dumps(row)}\n" for row in rows))

    # Do
    source = ClaudeSessionSource(source_path.parent)
    translated = source.translate(
        source.read(ClaudeSessionSummary(id=source_id, path=source_path))
    )

    # Assert
    assert [(message.role, message.content) for message in translated.messages] == [
        (Role.assistant, "visible answer"),
        (Role.user, "before"),
        (Role.tool, "ok"),
        (Role.user, "after"),
    ]
    assert all(message.reasoning_content is None for message in translated.messages)
    assistant = translated.messages[0]
    assert assistant.reasoning_payloads == [
        {
            "type": "thinking",
            "thinking": "private reasoning",
            "signature": "signature-1",
        }
    ]
    assert assistant.tool_calls is not None
    assert assistant.tool_calls[0].function.name == "Bash"
