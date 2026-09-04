from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
import io
import os
from pathlib import Path
import sys
from typing import cast

import pexpect
import pytest

from tests import TESTS_ROOT
from tests.e2e.common import write_e2e_config
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
def spawned_vibe_process() -> SpawnedVibeFactory:
    @contextmanager
    def spawn(
        workdir: Path, extra_args: Sequence[str] | None = None
    ) -> SpawnedVibeContext:
        captured = io.StringIO()
        env = os.environ.copy()
        env["VIBE_TEST_DISABLE_KEYRING"] = "1"
        env["VIBE_TEST_DISABLE_AUTO_TITLE"] = "1"
        arguments = ["--workdir", str(workdir), *(extra_args or [])]
        executable = "uv"
        if "--experimental-harness" in arguments:
            executable = str(Path(sys.executable).with_name("vibe"))
        else:
            arguments = ["run", "vibe", *arguments]
        child = pexpect.spawn(
            executable,
            arguments,
            cwd=str(TESTS_ROOT.parent),
            env=cast("os._Environ[str]", env),
            encoding="utf-8",
            timeout=30,
            dimensions=(36, 120),
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
