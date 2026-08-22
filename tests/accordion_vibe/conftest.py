"""Fixtures for the Accordion bridge suite.

Every test here drives a real subprocess (``fake_sidecar.py`` under
``sys.executable``) rather than a mock transport, because the interesting
behaviour lives in the pipes: a 250 ms timeout, a reader thread, a process that
dies mid-request. The seam that lets a Python script stand in for the Node
bundle is ``accordion_vibe._bridge.sidecar_command``, monkeypatched per test.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
import contextlib
from dataclasses import dataclass
import itertools
import json
from pathlib import Path
import sys
import time
from typing import Any

import pytest
import pytest_asyncio

from accordion_vibe import _bridge as bridge_module
from accordion_vibe._bridge import AccordionBridge, active_bridges
from accordion_vibe._loop import build_agent_loop
from accordion_vibe._sidecar import SidecarClient
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from tests.stubs.fake_mcp_registry import FakeMCPRegistry
from vibe.core.agent_loop import AgentLoop
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import VibeConfigSchema

FAKE_SIDECAR = Path(__file__).parent / "fake_sidecar.py"

# Long enough for a Python interpreter to boot and answer on a loaded CI box,
# short enough to stay far under the suite's 10 s per-test timeout.
HANDSHAKE_TIMEOUT_S = 5.0
WAIT_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class Sidecar:
    """One configured fake sidecar: the argv to spawn it and its recording."""

    argv: list[str]
    record: Path

    def received(self) -> list[dict[str, Any]]:
        """Every message the sidecar read off stdin, in order."""
        if not self.record.is_file():
            return []
        lines = self.record.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def received_of(self, kind: str) -> list[dict[str, Any]]:
        return [m for m in self.received() if m.get("type") == kind]


def wait_until(predicate: Callable[[], bool], timeout: float = WAIT_TIMEOUT_S) -> bool:
    """Poll ``predicate`` until it holds; state from the reader thread is async."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


@pytest.fixture(autouse=True)
def _neutral_accordion_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's own opt-in out of every activation decision.

    Only the two prefixed variables are cleared: bare ``HOME``/``APP`` are left
    alone on purpose, so that if ``AccordionConfig``'s ``env_prefix`` ever went
    missing again the inert-bridge tests would go red instead of being masked.
    """
    for name in ("ACCORDION_REPO", "ACCORDION_APP"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _no_leaked_bridges() -> Iterator[None]:
    """A bridge that outlives its test would be found by the next one's registry."""
    yield
    leaked = active_bridges()
    for bridge in leaked:
        with contextlib.suppress(Exception):
            bridge.close()
    assert not leaked, f"test leaked {len(leaked)} accordion bridge(s)"


@pytest.fixture
def make_sidecar(tmp_path: Path) -> Callable[..., Sidecar]:
    """Write a scenario file and return the argv that plays it."""
    counter = itertools.count()

    def _make(**scenario: Any) -> Sidecar:
        index = next(counter)
        record = tmp_path / f"sidecar-{index}.jsonl"
        path = tmp_path / f"scenario-{index}.json"
        path.write_text(
            json.dumps({**scenario, "record_path": str(record)}), encoding="utf-8"
        )
        return Sidecar(
            argv=[sys.executable, str(FAKE_SIDECAR), str(path)], record=record
        )

    return _make


@pytest.fixture
def accordion_repo(tmp_path: Path) -> Path:
    """A directory that passes ``resolve_accordion_repo``'s ``is_dir`` check."""
    home = tmp_path / "accordion-home"
    home.mkdir()
    return home


@pytest.fixture
def patch_sidecar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Callable[[Sidecar], None]:
    """Point the bridge's spawn + log seams at the fake sidecar and a temp log."""

    def _patch(sidecar: Sidecar) -> None:
        monkeypatch.setattr(
            bridge_module, "sidecar_command", lambda _home: list(sidecar.argv)
        )
        monkeypatch.setattr(
            bridge_module, "sidecar_log_path", lambda _sid: tmp_path / "sidecar.log"
        )

    return _patch


@pytest.fixture
def make_client(tmp_path: Path) -> Iterator[Callable[..., SidecarClient]]:
    """Build ``SidecarClient``s at the transport level and reap them all."""
    created: list[SidecarClient] = []

    def _make(sidecar: Sidecar, **kwargs: Any) -> SidecarClient:
        client = SidecarClient(list(sidecar.argv), cwd=tmp_path, **kwargs)
        created.append(client)
        return client

    yield _make
    for client in created:
        with contextlib.suppress(Exception):
            client.close()


@pytest.fixture
def make_bridge(
    tmp_path: Path, accordion_repo: Path, patch_sidecar: Callable[[Sidecar], None]
) -> Iterator[Callable[..., AccordionBridge]]:
    """Build started ``AccordionBridge``s and guarantee every one is closed."""
    created: list[AccordionBridge] = []

    def _make(
        sidecar: Sidecar, *, session_id: str = "session-1", start: bool = True
    ) -> AccordionBridge:
        patch_sidecar(sidecar)
        bridge = AccordionBridge(
            accordion_repo, session_id=session_id, cwd=tmp_path, harness_version="test"
        )
        created.append(bridge)
        if start:
            assert bridge.ensure_started(timeout=HANDSHAKE_TIMEOUT_S), (
                f"handshake failed: {bridge._client}"
            )
        return bridge

    yield _make
    for bridge in created:
        with contextlib.suppress(Exception):
            bridge.close()


@pytest_asyncio.fixture
async def make_loop(vibe_config: VibeConfigSchema) -> Any:
    """Build loops through the real ``build_agent_loop`` factory and close them."""
    loops: list[AgentLoop] = []

    def _make(
        *, config: VibeConfigSchema | None = None, backend: Any = None, **kwargs: Any
    ) -> AgentLoop:
        loop = build_agent_loop(
            config_orchestrator=FakeConfigOrchestrator(config or vibe_config),
            agent_name=BuiltinAgentName.ASK,
            backend=backend if backend is not None else FakeBackend(),
            enable_streaming=False,
            mcp_registry=FakeMCPRegistry(),
            **kwargs,
        )
        loops.append(loop)
        return loop

    yield _make
    for loop in loops:
        with contextlib.suppress(Exception):
            await loop.aclose()
