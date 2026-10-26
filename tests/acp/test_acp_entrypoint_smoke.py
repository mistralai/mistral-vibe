from __future__ import annotations

import asyncio
import asyncio.subprocess as aio_subprocess
from collections.abc import Iterator
import contextlib
import io
import os
from pathlib import Path
import signal
import socketserver
import subprocess
import sys
import sysconfig
import threading
from typing import Any, cast

from acp import (
    PROTOCOL_VERSION,
    Client,
    CreateElicitationResponse,
    RequestError,
    connect_to_agent,
)
from acp.schema import ClientCapabilities, Implementation
import pexpect
import pytest

from tests import TESTS_ROOT
from tests.e2e.common import ansi_tolerant_pattern

BROWSER_AUTH_NAME = "Sign in through Mistral AI Studio"
BROWSER_AUTH_DESCRIPTION = (
    "Sign into Mistral Vibe through your Mistral AI Studio account."
)
# The first answer pays the whole cold start of vibe-acp, with no network:
# about 4 s, up to 10 s when `-n auto` runs on a CPU-saturated machine.
_INITIALIZE_TIMEOUT_S = 20
_STEP_TIMEOUT_S = 10
# vibe-acp closes within about 1 s of SIGTERM, but a CPU-saturated `-n auto`
# run can starve it like the cold start; not exiting at all is a bug.
_EXIT_TIMEOUT_S = 20
_REAP_TIMEOUT_S = 5
# The longest test: initialize, two more steps, then the exit and the reap.
_TEST_TIMEOUT_S = (
    _INITIALIZE_TIMEOUT_S + 2 * _STEP_TIMEOUT_S + _EXIT_TIMEOUT_S + _REAP_TIMEOUT_S + 5
)
_STDERR_FILE_NAME = "vibe-acp.stderr"


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

    async def create_elicitation(
        self, message: str, mode: Any, **kwargs: Any
    ) -> CreateElicitationResponse:
        msg = "elicitation/create"
        raise RequestError.method_not_found(msg)

    async def complete_elicitation(self, elicitation_id: str, **kwargs: Any) -> None:
        msg = "elicitation/complete"
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


class _RefusingProxyHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        while self.rfile.readline() not in {b"\r\n", b"\n", b""}:
            pass
        self.wfile.write(
            b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        )


@pytest.fixture(autouse=True)
def _offline_vibe_acp(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # vibe-acp starts on the default Mistral provider with a mock key, so a
    # session start would call chat.mistral.ai and api.mistral.ai, and a network
    # stall, not vibe-acp, would decide whether a step meets its deadline.
    # The child inherits this environment and reaches no host but a proxy that
    # refuses every request at once, as if offline. A server owned by the test
    # cannot lose its port to another process, and answers at once where a
    # refused connect is retried (Windows).
    with socketserver.ThreadingTCPServer(
        ("127.0.0.1", 0), _RefusingProxyHandler
    ) as proxy:
        proxy.daemon_threads = True
        server = threading.Thread(target=proxy.serve_forever, args=(0.05,))
        server.start()
        proxy_url = f"http://127.0.0.1:{proxy.server_address[1]}"
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            monkeypatch.setenv(name, proxy_url)
            monkeypatch.setenv(name.lower(), proxy_url)
        for name in ("NO_PROXY", "no_proxy"):
            monkeypatch.delenv(name, raising=False)
        try:
            yield
        finally:
            proxy.shutdown()
            server.join()


def _vibe_acp_executable() -> str:
    # The environment's own entry point rather than `uv run vibe-acp`: no sync
    # can fail behind the refusing proxy, and the process the tests signal is
    # vibe-acp itself, not a uv wrapper that does not forward SIGKILL.
    name = "vibe-acp.exe" if os.name == "nt" else "vibe-acp"
    return str(Path(sysconfig.get_path("scripts")) / name)


async def _spawn_vibe_acp(
    env: dict[str, str], *, stderr_path: Path
) -> asyncio.subprocess.Process:
    with stderr_path.open("ab") as stderr:
        return await asyncio.create_subprocess_exec(
            _vibe_acp_executable(),
            stdin=aio_subprocess.PIPE,
            stdout=aio_subprocess.PIPE,
            stderr=stderr,
            cwd=TESTS_ROOT.parent,
            env=env,
            # Its own process group, so that killing it reaches its children.
            start_new_session=os.name == "posix",
        )


async def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError):
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        elif proc.returncode is None:
            proc.kill()
    # Bound the reap. Each test runs on its own function-scoped event loop
    # under xdist, and asyncio's child watcher can miss the SIGCHLD for a
    # process it spawned, leaving ``proc.wait()`` blocked forever even though
    # the process is already dead. An unbounded wait here would then run out
    # to the per-test timeout and surface as an opaque hang instead of the
    # test's real result. The process is killed; stop waiting after a grace
    # period and let the OS reap it.
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(proc.wait(), timeout=_REAP_TIMEOUT_S)


async def _terminate_process(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=_EXIT_TIMEOUT_S)
    exited = proc.returncode is not None
    await _kill_process_tree(proc)
    assert exited, (
        f"vibe-acp did not exit within {_EXIT_TIMEOUT_S} s of SIGTERM "
        f"(return code after SIGKILL: {proc.returncode}; its stderr is in "
        f"{_STDERR_FILE_NAME} under the test's tmp_path)"
    )


def _build_env(vibe_home_dir: Path, *, include_api_key: bool) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["VIBE_HOME"] = str(vibe_home_dir)
    env["VIBE_TEST_DISABLE_KEYRING"] = "1"

    vibe_home_dir.mkdir(parents=True, exist_ok=True)
    config_file = vibe_home_dir / "config.toml"
    if not config_file.exists():
        config_file.write_text("enable_telemetry = false\n")

    if include_api_key:
        env["MISTRAL_API_KEY"] = "mock"
    else:
        env.pop("MISTRAL_API_KEY", None)

    return env


def _build_client_capabilities(
    *, terminal_auth: bool = False, delegated_browser_auth: bool = False
) -> ClientCapabilities:
    if not terminal_auth and not delegated_browser_auth:
        return ClientCapabilities()

    field_meta: dict[str, bool] = {}
    if terminal_auth:
        field_meta["terminal-auth"] = True
    if delegated_browser_auth:
        field_meta["browser-auth-delegated"] = True
    return ClientCapabilities(field_meta=field_meta)


async def _connect_and_initialize(
    *,
    vibe_home_dir: Path,
    include_api_key: bool,
    terminal_auth: bool = False,
    delegated_browser_auth: bool = False,
) -> tuple[asyncio.subprocess.Process, Any, Any]:
    env = _build_env(vibe_home_dir, include_api_key=include_api_key)
    proc = await _spawn_vibe_acp(
        env, stderr_path=vibe_home_dir.parent / _STDERR_FILE_NAME
    )

    try:
        assert proc.stdin is not None
        assert proc.stdout is not None

        conn = connect_to_agent(_AcpSmokeClient(), proc.stdin, proc.stdout)
        initialize_response = await asyncio.wait_for(
            conn.initialize(
                protocol_version=PROTOCOL_VERSION,
                client_capabilities=_build_client_capabilities(
                    terminal_auth=terminal_auth,
                    delegated_browser_auth=delegated_browser_auth,
                ),
                client_info=Implementation(
                    name="pytest-smoke", title="Pytest Smoke", version="0.0.0"
                ),
            ),
            timeout=_INITIALIZE_TIMEOUT_S,
        )
    except Exception:
        await _kill_process_tree(proc)
        raise

    return proc, initialize_response, conn


@pytest.mark.asyncio
@pytest.mark.timeout(_TEST_TIMEOUT_S)
async def test_vibe_acp_initialize_and_new_session(vibe_home_dir: Path) -> None:
    proc, initialize_response, conn = await _connect_and_initialize(
        vibe_home_dir=vibe_home_dir, include_api_key=True
    )

    try:
        assert initialize_response.protocol_version == PROTOCOL_VERSION
        assert initialize_response.agent_info.name == "@mistralai/mistral-vibe"
        assert initialize_response.agent_info.title == "Mistral Vibe"

        config_schema = await asyncio.wait_for(
            conn.ext_method("config/schema", {}), timeout=_STEP_TIMEOUT_S
        )
        assert config_schema["version"].startswith("sha256:")
        assert config_schema["schema"]["title"] == "VibeConfigSchema"

        session = await asyncio.wait_for(
            conn.new_session(cwd=str(Path.cwd()), mcp_servers=[]),
            timeout=_STEP_TIMEOUT_S,
        )

        assert session.session_id
    finally:
        await _terminate_process(proc)


@pytest.mark.asyncio
@pytest.mark.timeout(_TEST_TIMEOUT_S)
async def test_vibe_acp_bootstraps_default_files(vibe_home_dir: Path) -> None:
    proc, _initialize_response, conn = await _connect_and_initialize(
        vibe_home_dir=vibe_home_dir, include_api_key=True
    )

    try:
        await asyncio.wait_for(
            conn.new_session(cwd=str(Path.cwd()), mcp_servers=[]),
            timeout=_STEP_TIMEOUT_S,
        )
    finally:
        await _terminate_process(proc)
    assert (vibe_home_dir / "config.toml").is_file()
    assert (vibe_home_dir / "vibehistory").is_file()


@pytest.mark.asyncio
@pytest.mark.timeout(_TEST_TIMEOUT_S)
async def test_vibe_acp_initialize_exposes_browser_auth(vibe_home_dir: Path) -> None:
    proc, initialize_response, _conn = await _connect_and_initialize(
        vibe_home_dir=vibe_home_dir, include_api_key=True
    )

    try:
        assert initialize_response.auth_methods is not None
        assert len(initialize_response.auth_methods) == 1
        auth_method = initialize_response.auth_methods[0]
        assert auth_method.id == "browser-auth"
        assert auth_method.name == BROWSER_AUTH_NAME
        assert auth_method.description == BROWSER_AUTH_DESCRIPTION
    finally:
        await _terminate_process(proc)


@pytest.mark.asyncio
@pytest.mark.timeout(_TEST_TIMEOUT_S)
async def test_vibe_acp_initialize_exposes_delegated_browser_auth_when_supported(
    vibe_home_dir: Path,
) -> None:
    proc, initialize_response, _conn = await _connect_and_initialize(
        vibe_home_dir=vibe_home_dir, include_api_key=True, delegated_browser_auth=True
    )

    try:
        assert initialize_response.auth_methods is not None
        assert len(initialize_response.auth_methods) == 2

        browser_auth_method = initialize_response.auth_methods[0]
        assert browser_auth_method.id == "browser-auth"
        assert browser_auth_method.name == BROWSER_AUTH_NAME
        assert browser_auth_method.description == BROWSER_AUTH_DESCRIPTION

        delegated_browser_auth_method = initialize_response.auth_methods[1]
        assert delegated_browser_auth_method.id == "browser-auth-delegated"
        assert delegated_browser_auth_method.name == BROWSER_AUTH_NAME
        assert delegated_browser_auth_method.description == BROWSER_AUTH_DESCRIPTION
    finally:
        await _terminate_process(proc)


@pytest.mark.asyncio
@pytest.mark.timeout(_TEST_TIMEOUT_S)
async def test_vibe_acp_initialize_exposes_terminal_auth_when_supported(
    vibe_home_dir: Path,
) -> None:
    proc, initialize_response, _conn = await _connect_and_initialize(
        vibe_home_dir=vibe_home_dir, include_api_key=True, terminal_auth=True
    )

    try:
        assert initialize_response.auth_methods is not None
        assert len(initialize_response.auth_methods) == 2

        browser_auth_method = initialize_response.auth_methods[0]
        assert browser_auth_method.id == "browser-auth"
        assert browser_auth_method.name == BROWSER_AUTH_NAME
        assert browser_auth_method.description == BROWSER_AUTH_DESCRIPTION

        auth_method = initialize_response.auth_methods[1]
        assert auth_method.id == "vibe-setup"
        assert auth_method.field_meta is not None

        terminal_auth = auth_method.field_meta["terminal-auth"]
        assert terminal_auth["label"] == "Mistral Vibe Setup"
        assert terminal_auth["command"]
        assert terminal_auth["args"]
        assert terminal_auth["args"][-1:] == ["--setup"]
    finally:
        await _terminate_process(proc)


@pytest.mark.timeout(_TEST_TIMEOUT_S)
def test_vibe_acp_setup_shows_onboarding_and_exits_on_cancel(
    vibe_home_dir: Path,
) -> None:
    pytest.importorskip("pty")
    env = cast("os._Environ[str]", _build_env(vibe_home_dir, include_api_key=False))
    env["TERM"] = "xterm-256color"

    captured = io.StringIO()
    child = pexpect.spawn(
        _vibe_acp_executable(),
        ["--setup"],
        cwd=str(TESTS_ROOT.parent),
        env=env,
        encoding="utf-8",
        timeout=10,
        dimensions=(36, 120),
    )
    child.logfile_read = captured

    try:
        child.expect(ansi_tolerant_pattern("Welcome to"), timeout=15)
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
@pytest.mark.timeout(_TEST_TIMEOUT_S)
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
                conn.new_session(cwd=str(Path.cwd()), mcp_servers=[]),
                timeout=_STEP_TIMEOUT_S,
            )
        assert proc.returncode is None, "Server crashed after broken config"

        (vibe_home_dir / "config.toml").write_text("")
        session = await asyncio.wait_for(
            conn.new_session(cwd=str(Path.cwd()), mcp_servers=[]),
            timeout=_STEP_TIMEOUT_S,
        )
        assert session.session_id
    finally:
        await _terminate_process(proc)


def test_acp_agent_import_does_not_load_gitpython() -> None:
    # GitPython probes for a git executable on import and raises when none is
    # found. The ACP startup path (e.g. `vibe-acp --version`) must not import it,
    # so binaries run on machines without git. See teleport lazy import.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, vibe.acp.agent; "
            "assert 'git' not in sys.modules, 'gitpython imported on ACP module load'",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
@pytest.mark.timeout(_TEST_TIMEOUT_S)
async def test_vibe_acp_new_session_fails_without_api_key(vibe_home_dir: Path) -> None:
    proc, _initialize_response, conn = await _connect_and_initialize(
        vibe_home_dir=vibe_home_dir, include_api_key=False
    )

    try:
        with pytest.raises(RequestError, match="Missing API key"):
            await asyncio.wait_for(
                conn.new_session(cwd=str(Path.cwd()), mcp_servers=[]),
                timeout=_STEP_TIMEOUT_S,
            )
    finally:
        await _terminate_process(proc)
