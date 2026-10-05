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
    # Tests marked unified_default characterize the unflagged binary — the
    # hard-default Unified Harness runtime — and spawn without any harness
    # flag; the rest of this suite still characterizes the legacy runtime.
    spawns_unified_default = (
        request.node.get_closest_marker("unified_default") is not None
    )

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
        if spawns_unified_default and any(
            flag in arguments for flag in ("--experimental-harness", "--legacy-harness")
        ):
            # A unified_default test passing a harness flag would silently take
            # the flag lane below and stop exercising the unflagged default —
            # fail loudly instead of quietly losing the coverage.
            raise ValueError(
                "unified_default tests must not pass harness-selection flags: "
                f"{arguments}"
            )
        executable = "uv"
        if "--experimental-harness" in arguments:
            executable = str(Path(sys.executable).with_name("vibe"))
        elif spawns_unified_default:
            arguments = ["run", "vibe", *arguments]
        else:
            # This suite still characterizes the legacy runtime, while the
            # unflagged default is the Unified Harness since VIBE-4903.
            # VIBE-4712 migrates these spawns to the unified default and
            # closes the gaps that migration surfaces.
            arguments = ["run", "vibe", "--legacy-harness", *arguments]
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
