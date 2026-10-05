from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import Any, cast

from pydantic import TypeAdapter
import pytest

from mistralai_vibe_local_harness.vibe._storage import _INTEROP_HISTORY_ADAPTER
from tests.conftest import build_test_vibe_config
from vibe.app_server._legacy_import import LegacySessionStore
from vibe.app_server.models import PublicMessageEntry, TextContentBlock
from vibe.app_server.protocol import SessionReadParams
from vibe.core.config import SessionLoggingConfig
from vibe.core.session.session_interop import InvalidLegacyInteropSourceError
from vibe.user_content import UserResource, UserTextResource
from vibe.utils.session_id import shorten_session_id

_RESOURCE_ADAPTER = TypeAdapter(UserResource)

SESSION_ID = "11111111-2222-3333-4444-555555555555"
# The session-index listing needs meta.json to describe a real legacy session.
METADATA: dict[str, Any] = {
    "session_id": SESSION_ID,
    "parent_session_id": None,
    "start_time": "2026-09-01T10:00:00+00:00",
    "end_time": None,
    "git_commit": None,
    "git_branch": "main",
    "environment": {"working_directory": "/workspace"},
    "origin_directory": "/workspace",
    "username": "user",
    "title": None,
    "bumped_at": None,
    "pinned_at": None,
    "config": {"active_model": "mistral-small-latest"},
    "total_messages": 0,
}
PLAIN_MESSAGES: list[dict[str, Any]] = [
    {"role": "user", "content": "run the tests"},
    {"role": "assistant", "content": "working"},
    {"role": "tool", "name": "shell", "tool_call_id": "call-1", "content": "ok"},
]


def _write_legacy_session(
    save_dir: Path,
    session_id: str,
    messages: list[dict[str, Any]],
    *,
    metadata_overrides: dict[str, Any] | None = None,
    messages_text: str | None = None,
    name_hint: str = "20260901",
) -> Path:
    """Write a legacy session folder the way older Vibe versions left it."""
    metadata = {**METADATA, "session_id": session_id}
    metadata["total_messages"] = len(messages)
    if metadata_overrides is not None:
        metadata.update(metadata_overrides)
    session_dir = save_dir / (f"session_{name_hint}_{shorten_session_id(session_id)}")
    session_dir.mkdir(parents=True)
    (session_dir / "meta.json").write_text(json.dumps(metadata))
    if messages_text is None:
        messages_text = "\n".join(json.dumps(message) for message in messages)
    (session_dir / "messages.jsonl").write_text(messages_text + "\n")
    return session_dir


def _store(tmp_path: Path) -> LegacySessionStore:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    return LegacySessionStore(config)


def _hash_tree(root: Path) -> dict[str, str]:
    """A byte-for-byte fingerprint of every file under *root*."""
    fingerprint: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            fingerprint[str(path.relative_to(root))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return fingerprint


def _read_params(session_id: str) -> SessionReadParams:
    return SessionReadParams(session_id=session_id, history=None, turns=None)


def test_supported_session_lists_and_reads(tmp_path: Path) -> None:
    _write_legacy_session(tmp_path, SESSION_ID, PLAIN_MESSAGES)
    store = _store(tmp_path)

    rows = store.list_sessions()
    assert [row.id for row in rows] == [SESSION_ID]
    assert rows[0].harness == "legacy"
    assert rows[0].preview == "run the tests"
    assert rows[0].cwd == "/workspace"

    response = store.read_session(_read_params(SESSION_ID))
    assert response is not None
    assert response.state.session.id == SESSION_ID
    assert response.state.history is not None
    first = response.state.history[0]
    assert isinstance(first, PublicMessageEntry)
    assert first.role == "user"
    assert isinstance(first.content[0], TextContentBlock)
    assert first.content[0].text == "run the tests"


def test_loader_returns_quiescent_export_and_source_is_unchanged(
    tmp_path: Path,
) -> None:
    session_dir = _write_legacy_session(tmp_path, SESSION_ID, PLAIN_MESSAGES)
    before = _hash_tree(session_dir)
    store = _store(tmp_path)

    source = store.loader()(SESSION_ID)
    assert source.state == "quiescent"
    assert source.reference is not None
    assert source.reference.session_id == SESSION_ID
    assert source.reference.cwd == "/workspace"
    assert len(source.store_revision or "") == 64
    assert source.active_model == "mistral-small-latest"
    assert [message["role"] for message in source.history or []] == [
        "user",
        "assistant",
        "tool",
    ]
    # The host validates the exported history with this adapter before it
    # copies anything into a new Unified session.
    assert _INTEROP_HISTORY_ADAPTER.validate_python(source.history)

    reference = store.resolver()(SESSION_ID)
    assert reference is not None
    assert reference.session_id == SESSION_ID

    assert _hash_tree(session_dir) == before


def test_kindless_resource_export_stays_parseable(tmp_path: Path) -> None:
    """VIBE-4790: the export writes MCP-style resource blocks with no ``kind``.

    A session carrying resources must export that shape, and the tolerant
    read must keep parsing it so a resumed import never fails.
    """
    messages = [
        {
            "role": "user",
            "content": "read the report",
            "resources": [
                {
                    "kind": "text",
                    "uri": "file:///project/report.md",
                    "media_type": "text/markdown",
                    "text": "# Report",
                }
            ],
        }
    ]
    _write_legacy_session(tmp_path, SESSION_ID, messages)
    store = _store(tmp_path)

    source = store.loader()(SESSION_ID)
    assert source.state == "quiescent"
    assert source.history is not None
    exported_user = cast(dict[str, Any], source.history[0])
    block = next(
        item
        for item in cast(list[dict[str, Any]], exported_user["content"])
        if item.get("type") == "resource"
    )
    assert block["resource"] == {
        "uri": "file:///project/report.md",
        "mimeType": "text/markdown",
        "text": "# Report",
    }
    resource = _RESOURCE_ADAPTER.validate_python(block["resource"])
    assert isinstance(resource, UserTextResource)
    assert resource.media_type == "text/markdown"
    assert _INTEROP_HISTORY_ADAPTER.validate_python(source.history)


def test_absent_session_reports_absent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    missing = "99999999-8888-7777-6666-555555555555"

    assert store.loader()(missing).state == "absent"
    assert store.resolver()(missing) is None
    assert store.read_session(_read_params(missing)) is None


def test_unloadable_candidate_reports_invalid(tmp_path: Path) -> None:
    _write_legacy_session(
        tmp_path,
        SESSION_ID,
        PLAIN_MESSAGES,
        messages_text='{"role": "user", "content": "unterminated',
    )
    store = _store(tmp_path)

    source = store.loader()(SESSION_ID)
    assert source.state == "invalid"
    assert source.error is not None
    assert store.read_session(_read_params(SESSION_ID)) is None


def test_identity_mismatch_reports_invalid(tmp_path: Path) -> None:
    _write_legacy_session(
        tmp_path,
        SESSION_ID,
        PLAIN_MESSAGES,
        metadata_overrides={"session_id": "00000000-0000-0000-0000-000000000000"},
    )
    store = _store(tmp_path)

    source = store.loader()(SESSION_ID)
    assert source.state == "invalid"
    assert "cannot be loaded" in (source.error or "")


def test_ambiguous_short_id_reports_invalid(tmp_path: Path) -> None:
    """Two canonical sessions share one short id: only the short id is asked for."""
    for suffix in ("aaaa", "bbbb"):
        session_id = f"11111111-2222-3333-4444-555555555{suffix}"
        _write_legacy_session(
            tmp_path, session_id, PLAIN_MESSAGES, name_hint=f"20260901{suffix}"
        )
    store = _store(tmp_path)

    source = store.loader()(shorten_session_id(SESSION_ID))
    assert source.state == "invalid"
    assert "ambiguous" in (source.error or "").lower()
    with pytest.raises(InvalidLegacyInteropSourceError):
        store.resolver()(shorten_session_id(SESSION_ID))


def test_symlinked_source_reports_invalid(tmp_path: Path) -> None:
    real_dir = tmp_path / "real"
    _write_legacy_session(real_dir, SESSION_ID, PLAIN_MESSAGES)
    link_dir = tmp_path / f"session_20260901_{shorten_session_id(SESSION_ID)}"
    link_dir.symlink_to(real_dir / f"session_20260901_{shorten_session_id(SESSION_ID)}")
    store = _store(tmp_path)

    source = store.loader()(SESSION_ID)
    assert source.state == "invalid"
    assert "symbolic link" in (source.error or "")


def test_import_graph_reaches_no_legacy_execution_module(tmp_path: Path) -> None:
    """The importer runs in a fresh interpreter with legacy code blocked.

    If the module, or anything it pulls in, imports the legacy execution
    engine (or the app-server modules that host it), the import itself
    fails. The blocker stays installed while the store reads a real
    fixture, so lazy imports are covered too.
    """
    script = textwrap.dedent(
        """
        import json
        import sys
        from importlib.abc import MetaPathFinder

        FORBIDDEN = (
            "vibe.core.agent_loop",
            "vibe.app_server._legacy_session_backend",
            "vibe.app_server._legacy_session_runtime",
            "vibe.app_server._legacy_composition",
            "vibe.app_server._host",
            "vibe.app_server._handler",
            "vibe.app_server._utils",
        )


        class _Blocker(MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                for name in FORBIDDEN:
                    if fullname == name or fullname.startswith(name + "."):
                        raise ImportError(
                            f"forbidden import reached: {fullname}"
                        )
                return None


        sys.meta_path.insert(0, _Blocker())

        from vibe.core.config.harness_files import init_harness_files_manager

        init_harness_files_manager("user", "project")

        from vibe.core.config import SessionLoggingConfig, VibeConfigSchema
        from vibe.app_server._legacy_import import LegacySessionStore
        from vibe.app_server.protocol import SessionReadParams

        session_id = "11111111-2222-3333-4444-555555555555"
        save_dir = sys.argv[1]
        session_dir = f"{save_dir}/session_20260901_11111111"
        metadata = {
            "session_id": session_id,
            "start_time": "2026-09-01T10:00:00+00:00",
            "end_time": None,
            "git_commit": None,
            "git_branch": "main",
            "environment": {"working_directory": "/workspace"},
            "origin_directory": "/workspace",
            "username": "user",
            "total_messages": 1,
        }
        import pathlib

        pathlib.Path(session_dir).mkdir(parents=True)
        pathlib.Path(session_dir, "meta.json").write_text(json.dumps(metadata))
        pathlib.Path(session_dir, "messages.jsonl").write_text(
            json.dumps({"role": "user", "content": "hello"}) + "\\n"
        )

        config = VibeConfigSchema(
            session_logging=SessionLoggingConfig(
                enabled=True, save_dir=save_dir, session_prefix="session"
            ),
            enable_connectors=False,
        )
        store = LegacySessionStore(config)
        rows = store.list_sessions()
        assert rows and rows[0].harness == "legacy", rows

        preview = store.read_session(
            SessionReadParams(session_id=session_id, history=None, turns=None)
        )
        assert preview is not None

        source = store.loader()(session_id)
        assert source.state == "quiescent", source

        reference = store.resolver()(session_id)
        assert reference is not None and reference.session_id == session_id
        print("ok")
        """
    )
    project_dir = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=project_dir,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
