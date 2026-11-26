"""``session/relocate`` on a session no agent holds, answered from its record."""

from __future__ import annotations

from pathlib import Path
import tomllib

import pytest
import tomli_w

from tests.stubs.historical_session import write_historical_session
from vibe.app_server._connection_protocol import ClientInfo
from vibe.app_server._dispatch import RequestFailure
from vibe.app_server._host import HostRequestHandler
from vibe.app_server._process_services import ProcessServices
from vibe.app_server.protocol import ProtocolErrorCode, SessionRelocateResponse
from vibe.core.config import VibeConfigSchema
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.types import LLMMessage, Role

_SESSION_ID = "0f0e0d0c-0000-4000-8000-000000000001"


def _saved_session(config_dir: Path, tmp_path: Path, cwd: Path) -> None:
    config_file = config_dir / "config.toml"
    raw = tomllib.loads(config_file.read_text(encoding="utf-8"))
    raw["session_logging"] = {"enabled": True, "save_dir": str(tmp_path / "logs")}
    config_file.write_text(tomli_w.dumps(raw), encoding="utf-8")
    write_historical_session(
        config=VibeConfigSchema.model_validate(raw),
        session_id=_SESSION_ID,
        messages=[LLMMessage(role=Role.user, content="hello")],
        cwd=cwd,
    )


async def _relocate(
    process_services: ProcessServices, cwd: Path
) -> SessionRelocateResponse:
    handler = HostRequestHandler(
        HarnessFilesManager(sources=("user",)),
        org_config=process_services.org_config,
        worktrees=process_services.worktrees,
        experiments=process_services.experiments,
        model_catalog=process_services.model_catalog,
    )
    result = await handler.dispatch(
        "session/relocate",
        {"sessionId": _SESSION_ID, "cwd": str(cwd)},
        client_info=ClientInfo(name="test", version="0"),
    )
    assert isinstance(result.response, SessionRelocateResponse)
    return result.response


@pytest.mark.asyncio
async def test_a_saved_session_moves_to_a_directory_outside_its_repository(
    config_dir: Path, tmp_path: Path, process_services: ProcessServices
) -> None:
    """*Prepare*: A saved session in a folder that is no git repository.
    *Do*: Relocate it to another existing folder.
    *Assert*: The record moves there.
    """
    # Prepare
    origin = (tmp_path / "origin").resolve()
    target = (tmp_path / "target").resolve()
    origin.mkdir()
    target.mkdir()
    _saved_session(config_dir, tmp_path, origin)

    # Do
    response = await _relocate(process_services, target)

    # Assert
    assert response.state.session.cwd == str(target)


@pytest.mark.asyncio
async def test_a_saved_session_is_not_moved_to_a_missing_directory(
    config_dir: Path, tmp_path: Path, process_services: ProcessServices
) -> None:
    """*Prepare*: A saved session.
    *Do*: Relocate it to a folder that does not exist.
    *Assert*: Invalid params, naming the folder.
    """
    # Prepare
    origin = (tmp_path / "origin").resolve()
    origin.mkdir()
    _saved_session(config_dir, tmp_path, origin)
    target = tmp_path / "missing"

    # Do
    with pytest.raises(RequestFailure) as raised:
        await _relocate(process_services, target)

    # Assert
    assert raised.value.code is ProtocolErrorCode.INVALID_PARAMS
    assert str(raised.value) == f"Not a directory: {target.resolve()}"
