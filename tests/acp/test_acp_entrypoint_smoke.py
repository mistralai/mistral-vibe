from __future__ import annotations

import asyncio
import asyncio.subprocess as aio_subprocess
import contextlib
import io
import os
from pathlib import Path
from typing import Any, cast

from acp import PROTOCOL_VERSION, Client, RequestError, connect_to_agent
from acp.schema import ClientCapabilities, Implementation
import pexpect
import pytest

from tests import TESTS_ROOT
from tests.e2e.common import ansi_tolerant_pattern


class _AcpSmokeClient(Client):
    def on_connect(self, conn: Any) -> None:
        pass

    async def request_permission(self, *args: Any, **kwargs: Any) -> Any:
        msg = "session/request_permission"
        raise RequestError.method_not_found(msg)

    async def write_text_file(self, *args: Any, **kwargs: Any) -> Any:
        msg = "fs/write_text_file"
        raise RequestError.method_not_found(msg)

    async def read_text_file(self, *args: Any, **kwargs: Any) -> Any:
        msg = "fs/read_text_file"
        raise RequestError.method_not_found(msg)

    async def create_terminal(self, *args: Any, **kwargs: Any) -> Any:
        msg = "terminal/create"
        raise RequestError.method_not_found(msg)

    async def terminal_output(self, *args: Any, **kwargs: Any) -> Any:
        msg = "terminal/output"
        raise RequestError.method_not_found(msg)

    async def release_terminal(self, *args: Any, **kwargs: Any) -> Any:
        msg = "terminal/release"
        raise RequestError.method_not_found(msg)

    async def wait_for_terminal_exit(self, *args: Any, **kwargs: Any) -> Any:
        msg = "terminal/wait_for_exit"
        raise RequestError.method_not_found(msg)

    async def kill_terminal(self, *args: Any, **kwargs: Any) -> Any:
        msg = "terminal/kill"
        raise RequestError.method_not_found(msg)

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        _ = params
        raise RequestError.method_not_found(method)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        _ = params
        raise RequestError.method_not_found(method)

    async def session_update(self, *_args: Any, **_kwargs: Any) -> None:
        pass


@pytest.fixture
def vibe_home_dir(tmp_path: Path) -> Path:
    return tmp_path / ".vibe"


async def _spawn_vibe_acp(env: dict[str, str]) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        "uv",
        "run",
        "vibe-acp",
        stdin=aio_subprocess.PIPE,
        stdout=aio_subprocess.PIPE,
        stderr=aio_subprocess.PIPE,
        cwd=TESTS_ROOT.parent,
        env=env,
    )


async def _terminate_process(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=5)

    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
            await proc.wait()


def _build_env(vibe_home_dir: Path, *, include_api_key: bool) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["VIBE_HOME"] = str(vibe_home_dir)

    if include_api_key:
        env["MISTRAL_API_KEY"] = "mock"
    else:
        env.pop("MISTRAL_API_KEY", None)

    return env


def _build_client_capabilities(*, terminal_auth: bool = False) -> ClientCapabilities:
    if not terminal_auth:
        return ClientCapabilities()

    return ClientCapabilities(field_meta={"terminal-auth": True})


async def _connect_and_initialize(
    *, vibe_home_dir: Path, include_api_key: bool, terminal_auth: bool = False
) -> tuple[asyncio.subprocess.Process, Any, Any]:
    env = _build_env(vibe_home_dir, include_api_key=include_api_key)
    proc = await _spawn_vibe_acp(env)

    try:
        assert proc.stdin is not None
        assert proc.stdout is not None

        conn = connect_to_agent(_AcpSmokeClient(), proc.stdin, proc.stdout)
        initialize_response = await asyncio.wait_for(
            conn.initialize(
                protocol_version=PROTOCOL_VERSION,
                client_capabilities=_build_client_capabilities(
                    terminal_auth=terminal_auth
                ),
                client_info=Implementation(
                    name="pytest-smoke", title="Pytest Smoke", version="0.0.0"
                ),
            ),
            timeout=10,
        )
    except Exception:
        await _terminate_process(proc)
        raise

    return proc, initialize_response, conn


@pytest.mark.asyncio
async def test_vibe_acp_initialize_and_new_session(vibe_home_dir: Path) -> None:
    proc, initialize_response, conn = await _connect_and_initialize(
        vibe_home_dir=vibe_home_dir, include_api_key=True
    )

    try:
        assert initialize_response.protocol_version == PROTOCOL_VERSION
        assert initialize_response.agent_info.name == "@mistralai/mistral-vibe"
        assert initialize_response.agent_info.title == "Mistral Vibe"

        session = await asyncio.wait_for(
            conn.new_session(cwd=str(Path.cwd()), mcp_servers=[]), timeout=10
        )

        assert session.session_id
    finally:
        await _terminate_process(proc)


@pytest.mark.asyncio
async def test_vibe_acp_bootstraps_default_files(vibe_home_dir: Path) -> None:
    proc, _initialize_response, conn = await _connect_and_initialize(
        vibe_home_dir=vibe_home_dir, include_api_key=True
    )

    try:
        await asyncio.wait_for(
            conn.new_session(cwd=str(Path.cwd()), mcp_servers=[]), timeout=10
        )
    finally:
        await _terminate_process(proc)
    assert (vibe_home_dir / "config.toml").is_file()
    assert (vibe_home_dir / "vibehistory").is_file()


@pytest.mark.asyncio
async def test_vibe_acp_initialize_exposes_terminal_auth_when_supported(
    vibe_home_dir: Path,
) -> None:
    proc, initialize_response, conn = await _connect_and_initialize(
        vibe_home_dir=vibe_home_dir, include_api_key=True, terminal_auth=True
    )

    try:
        assert initialize_response.auth_methods is not None
        assert len(initialize_response.auth_methods) == 1

        auth_method = initialize_response.auth_methods[0]
        assert auth_method.id == "vibe-setup"
        assert auth_method.field_meta is not None

        terminal_auth = auth_method.field_meta["terminal-auth"]
        assert terminal_auth["label"] == "Mistral Vibe Setup"
        assert terminal_auth["command"]
        assert terminal_auth["args"]
    finally:
        await _terminate_process(proc)


@pytest.mark.timeout(15)
def test_vibe_acp_setup_shows_onboarding_and_exits_on_cancel(
    vibe_home_dir: Path,
) -> None:
    env = cast("os._Environ[str]", _build_env(vibe_home_dir, include_api_key=False))
    env["TERM"] = "xterm-256color"

    captured = io.StringIO()
    child = pexpect.spawn(
        "uv",
        ["run", "vibe-acp", "--setup"],
        cwd=str(TESTS_ROOT.parent),
        env=env,
        encoding="utf-8",
        timeout=10,
        dimensions=(36, 120),
    )
    child.logfile_read = captured

    try:
        child.expect(ansi_tolerant_pattern("Welcome to Mistral Vibe"), timeout=10)
        child.sendcontrol("c")
        child.expect(pexpect.EOF, timeout=10)
    finally:
        if child.isalive():
            child.terminate(force=True)
        if not child.closed:
            child.close()

    output = captured.getvalue()
    assert "Setup cancelled" in output


@pytest.mark.asyncio
async def test_vibe_acp_survives_broken_config(vibe_home_dir: Path) -> None:
    vibe_home_dir.mkdir(parents=True, exist_ok=True)
    (vibe_home_dir / "config.toml").write_text("{{{{invalid toml content!!")

    proc, _initialize_response, conn = await _connect_and_initialize(
        vibe_home_dir=vibe_home_dir, include_api_key=True
    )

    try:
        # new_session should return a structured JSON-RPC error, not crash the server
        with pytest.raises(RequestError):
            await asyncio.wait_for(
                conn.new_session(cwd=str(Path.cwd()), mcp_servers=[]), timeout=10
            )
        assert proc.returncode is None, "Server crashed after broken config"

        (vibe_home_dir / "config.toml").write_text("")
        session = await asyncio.wait_for(
            conn.new_session(cwd=str(Path.cwd()), mcp_servers=[]), timeout=10
        )
        assert session.session_id
    finally:
        await _terminate_process(proc)


@pytest.mark.asyncio
async def test_vibe_acp_new_session_fails_without_api_key(vibe_home_dir: Path) -> None:
    proc, _initialize_response, conn = await _connect_and_initialize(
        vibe_home_dir=vibe_home_dir, include_api_key=False
    )

    try:
        with pytest.raises(RequestError, match="Missing API key"):
            await asyncio.wait_for(
                conn.new_session(cwd=str(Path.cwd()), mcp_servers=[]), timeout=10
            )
    finally:
        await _terminate_process(proc)
