"""Which runtime owns a stored session, and the opt-in for the target runtime."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import json
from pathlib import Path
from typing import Any

import pytest

from tests.app_server.test_unified_harness_backend_adapter import (
    _harness_backend_host,
    _persist_with_a_turn,
)
from tests.conftest import build_test_vibe_config
from vibe.app_server._session_backend_port import SessionBackendError
from vibe.app_server._session_runtime_owner import TARGET_RUNTIME_UNSUPPORTED
from vibe.app_server.models import TextContentBlock
from vibe.app_server.protocol import (
    ProtocolErrorCode,
    SessionContinueParams,
    SessionForkParams,
    SessionHistoryClearParams,
    SessionKind,
    SessionOptions,
    SessionResumeParams,
    SessionStartParams,
    SessionStartTurn,
    TurnStartParams,
)
from vibe.core.config import SessionLoggingConfig, VibeConfigSchema

type _Open = Callable[[Any, str, Path], Awaitable[object]]


def _config(tmp_path: Path, *, opted_in: bool = False) -> VibeConfigSchema:
    return build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path)),
        experimental_target_runtime=opted_in,
    )


def _session_dir(tmp_path: Path, session_id: str) -> Path:
    return tmp_path / "unified" / session_id


def _read_meta(tmp_path: Path, session_id: str) -> dict[str, Any]:
    return json.loads((_session_dir(tmp_path, session_id) / "meta.json").read_text())


def _write_meta(tmp_path: Path, session_id: str, metadata: dict[str, Any]) -> None:
    (_session_dir(tmp_path, session_id) / "meta.json").write_text(json.dumps(metadata))


async def _stored_session(tmp_path: Path, cwd: Path) -> str:
    host = _harness_backend_host(_config(tmp_path))
    started = await host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=str(cwd)))
    )
    await _persist_with_a_turn(started.backend, "persist me")
    await host.shutdown()
    return started.backend.session_id


def _assert_refused(error: SessionBackendError) -> None:
    assert error.code is ProtocolErrorCode.NOT_IMPLEMENTED
    assert error.data == {"reason": TARGET_RUNTIME_UNSUPPORTED}


async def _resume(host: Any, session_id: str, _cwd: Path) -> object:
    return await host.resume(SessionResumeParams(session_id=session_id))


async def _continue(host: Any, _session_id: str, cwd: Path) -> object:
    return await host.continue_latest(
        SessionContinueParams(agent_config=SessionOptions(cwd=str(cwd)))
    )


async def _fork(host: Any, session_id: str, _cwd: Path) -> object:
    return await host.fork(SessionForkParams(source_session_id=session_id))


_OPENS = [
    pytest.param(_resume, id="resume"),
    pytest.param(_continue, id="continue"),
    pytest.param(_fork, id="fork"),
]


@pytest.fixture
def cwd(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    return project


@pytest.mark.asyncio
async def test_a_new_session_records_the_local_runtime_as_its_owner(
    tmp_path: Path, cwd: Path
) -> None:
    """*Prepare*: The opt-in off, its default.
    *Do*: Start a session and save a turn.
    *Assert*: Its ``meta.json`` names the local runtime as its owner.
    """
    # Do
    session_id = await _stored_session(tmp_path, cwd)

    # Assert
    assert _read_meta(tmp_path, session_id)["runtime"] == "local"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        pytest.param(SessionKind.NORMAL, id="new session"),
        pytest.param(SessionKind.EPHEMERAL, id="warm-up"),
    ],
)
async def test_the_opt_in_opens_a_session_and_refuses_its_first_message(
    tmp_path: Path, cwd: Path, kind: SessionKind
) -> None:
    """*Prepare*: The opt-in on, and a new session or a warm-up started.
    *Do*: Send it a message.
    *Assert*: The start succeeds, the message is refused, and nothing is saved.
    """
    # Prepare
    host = _harness_backend_host(_config(tmp_path, opted_in=True))
    started = await host.start(
        SessionStartParams(kind=kind, agent_config=SessionOptions(cwd=str(cwd)))
    )

    # Do
    with pytest.raises(SessionBackendError) as exc_info:
        await started.backend.start_turn(
            TurnStartParams(
                session_id=started.backend.session_id,
                message=[TextContentBlock(text="hello")],
            )
        )
    await host.shutdown()

    # Assert
    _assert_refused(exc_info.value)
    assert "VIBE_EXPERIMENTAL_TARGET_RUNTIME" in str(exc_info.value)
    assert not _session_dir(tmp_path, started.backend.session_id).exists()


@pytest.mark.asyncio
async def test_the_opt_in_refuses_a_start_that_carries_its_first_message(
    tmp_path: Path, cwd: Path
) -> None:
    """*Prepare*: The opt-in on.
    *Do*: Start a session with its first message.
    *Assert*: The start is refused and nothing is saved.
    """
    # Prepare
    host = _harness_backend_host(_config(tmp_path, opted_in=True))
    task = SessionStartTurn(message=[TextContentBlock(text="Fix it")])

    # Do
    with pytest.raises(SessionBackendError) as exc_info:
        await host.start(
            SessionStartParams(
                first_turn=task, agent_config=SessionOptions(cwd=str(cwd))
            )
        )
    await host.shutdown()

    # Assert
    _assert_refused(exc_info.value)
    assert not (tmp_path / "unified").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("open_session", _OPENS)
async def test_the_opt_in_leaves_local_sessions_usable(
    tmp_path: Path, cwd: Path, open_session: _Open
) -> None:
    """*Prepare*: A session saved by the local runtime, and the opt-in on.
    *Do*: Resume, continue or fork it.
    *Assert*: It opens, and the local runtime stays its owner.
    """
    # Prepare
    session_id = await _stored_session(tmp_path, cwd)
    host = _harness_backend_host(_config(tmp_path, opted_in=True))

    # Do
    await open_session(host, session_id, cwd)
    await host.shutdown()

    # Assert
    assert _read_meta(tmp_path, session_id)["runtime"] == "local"


@pytest.mark.asyncio
async def test_a_session_cleared_with_the_opt_in_on_stays_local(
    tmp_path: Path, cwd: Path
) -> None:
    """*Prepare*: A local session open, and the opt-in on.
    *Do*: Clear its history, which replaces it with a new session.
    *Assert*: The replacement is local, like its source.
    """
    # Prepare
    session_id = await _stored_session(tmp_path, cwd)
    host: Any = _harness_backend_host(_config(tmp_path, opted_in=True))
    source = await host.resume(SessionResumeParams(session_id=session_id))

    # Do
    cleared = await host.clear_history(
        source.backend, SessionHistoryClearParams(session_id=session_id)
    )
    await _persist_with_a_turn(cleared.backend, "after clear")
    await host.shutdown()

    # Assert
    assert _read_meta(tmp_path, cleared.backend.session_id)["runtime"] == "local"


@pytest.mark.asyncio
@pytest.mark.parametrize("open_session", _OPENS)
@pytest.mark.parametrize(
    "owner",
    [
        pytest.param({"runtime": "target"}, id="target"),
        pytest.param(
            {"runtime": "cloud", "title": ["not", "a", "title"]},
            id="a runtime and a field this build cannot read",
        ),
    ],
)
async def test_a_session_the_local_runtime_does_not_own_is_refused(
    tmp_path: Path, cwd: Path, open_session: _Open, owner: dict[str, Any]
) -> None:
    """*Prepare*: A saved session whose ``meta.json`` names another runtime.
    *Do*: Resume, continue or fork it.
    *Assert*: Refused, with ``meta.json`` untouched.
    """
    # Prepare
    session_id = await _stored_session(tmp_path, cwd)
    _write_meta(tmp_path, session_id, {**_read_meta(tmp_path, session_id), **owner})
    before = (_session_dir(tmp_path, session_id) / "meta.json").read_bytes()
    host = _harness_backend_host(_config(tmp_path))

    # Do
    with pytest.raises(SessionBackendError) as exc_info:
        await open_session(host, session_id, cwd)
    await host.shutdown()

    # Assert
    _assert_refused(exc_info.value)
    assert (_session_dir(tmp_path, session_id) / "meta.json").read_bytes() == before


@pytest.mark.asyncio
async def test_a_session_saved_before_owners_were_recorded_opens_as_local(
    tmp_path: Path, cwd: Path
) -> None:
    """*Prepare*: A saved session with no owner, as older builds write it.
    *Do*: Resume it and save a turn.
    *Assert*: It opens, and ``"local"`` is written as its owner.
    """
    # Prepare
    session_id = await _stored_session(tmp_path, cwd)
    metadata = _read_meta(tmp_path, session_id)
    del metadata["runtime"]
    _write_meta(tmp_path, session_id, metadata)
    host = _harness_backend_host(_config(tmp_path))

    # Do
    resumed = await host.resume(SessionResumeParams(session_id=session_id))
    await _persist_with_a_turn(resumed.backend, "after the upgrade")
    await host.shutdown()

    # Assert
    assert _read_meta(tmp_path, session_id)["runtime"] == "local"
