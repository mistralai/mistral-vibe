from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
import io
import os
from pathlib import Path
from typing import cast
import zipfile

import pexpect
import pytest

from tests import TESTS_ROOT
from tests.e2e.common import (
    RUST_CLI_ENV,
    TERMINAL_COLUMNS,
    TERMINAL_ROWS,
    write_e2e_config,
)
from tests.e2e.mock_server import ChunkFactory, StreamingMockServer


@pytest.fixture
def streaming_mock_server(
    request: pytest.FixtureRequest,
) -> Iterator[StreamingMockServer]:
    chunk_factory = cast(ChunkFactory | None, getattr(request, "param", None))
    server = StreamingMockServer(chunk_factory=chunk_factory)
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def setup_e2e_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    streaming_mock_server: StreamingMockServer,
) -> None:
    vibe_home = tmp_path / "vibe-home"
    write_e2e_config(vibe_home, streaming_mock_server.api_base)
    monkeypatch.setenv("MISTRAL_API_KEY", "fake-key")
    monkeypatch.setenv("VIBE_HOME", str(vibe_home))
    monkeypatch.setenv("VIBE_TEST_DISABLE_KEYRING", "1")
    monkeypatch.setenv("VIBE_TEST_DISABLE_AUTO_TITLE", "1")
    monkeypatch.setenv("VIBE_TEST_DISABLE_MODEL_PROBE", "1")
    monkeypatch.setenv("TERM", "xterm-256color")


@pytest.fixture
def e2e_workdir(tmp_path: Path) -> Path:
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    return workdir


type SpawnedVibeContext = Iterator[tuple[pexpect.spawn, io.StringIO]]
type SpawnedVibeContextManager = AbstractContextManager[
    tuple[pexpect.spawn, io.StringIO]
]
type SpawnedVibeFactory = Callable[
    [Path, Sequence[str] | None], SpawnedVibeContextManager
]


@pytest.fixture
def spawned_vibe_process(request: pytest.FixtureRequest) -> SpawnedVibeFactory:
    pytest.importorskip("pty")

    @contextmanager
    def spawn(
        workdir: Path, extra_args: Sequence[str] | None = None
    ) -> SpawnedVibeContext:
        captured = io.StringIO()
        env = os.environ.copy()
        env["VIBE_TEST_DISABLE_KEYRING"] = "1"
        env["VIBE_TEST_DISABLE_AUTO_TITLE"] = "1"
        env["VIBE_TEST_DISABLE_MODEL_PROBE"] = "1"
        arguments = ["--workdir", str(workdir), *(extra_args or [])]
        child = pexpect.spawn(
            "uv",
            ["run", "--no-sync", "vibe", *arguments],
            cwd=str(TESTS_ROOT.parent),
            env=cast("os._Environ[str]", env),
            encoding="utf-8",
            timeout=30,
            dimensions=(TERMINAL_ROWS, TERMINAL_COLUMNS),
        )
        child.logfile_read = captured

        try:
            yield child, captured
        finally:
            if child.isalive():
                child.terminate(force=True)
            if not child.closed:
                child.close()

    return spawn


# CI's e2e lane has no cargo, but the combined wheel it assembles bundles the CLI.
_PREBUILT_WHEEL_ENV = "VIBE_PREBUILT_WHEEL"
_RUST_CLI_NAME = "vibe-rs.exe" if os.name == "nt" else "vibe-rs"
_RELEASE_RUST_CLI = (
    TESTS_ROOT.parent / "vibe" / "cli-rust" / "target" / "release" / _RUST_CLI_NAME
)


@pytest.fixture(scope="session")
def rust_cli_executable(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The Rust CLI: the one the combined wheel bundles, else a local release build."""
    if prebuilt := os.environ.get(_PREBUILT_WHEEL_ENV):
        member = f"vibe/_bin/{_RUST_CLI_NAME}"
        executable = tmp_path_factory.mktemp("rust-cli") / _RUST_CLI_NAME
        with zipfile.ZipFile(prebuilt) as wheel:
            executable.write_bytes(wheel.read(member))
        executable.chmod(0o755)
        return executable
    if _RELEASE_RUST_CLI.exists():
        return _RELEASE_RUST_CLI
    pytest.skip(f"No Rust CLI: set {_PREBUILT_WHEEL_ENV} or run `make build`")


@pytest.fixture
def rust_cli(rust_cli_executable: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Make `run_rust_vibe` run the Rust CLI."""
    monkeypatch.setenv(RUST_CLI_ENV, str(rust_cli_executable))
    return rust_cli_executable
