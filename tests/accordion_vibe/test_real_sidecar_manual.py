"""Manual end-to-end against the REAL Node sidecar.

Skipped unless ACCORDION_E2E_REPO points at an Accordion checkout with a built
`extension/sidecar.mjs` and node on PATH. Kept out of the default run because
it depends on a machine-local Node build, not on anything in this repo.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil

import pytest

from accordion_vibe._bridge import AccordionBridge
from accordion_vibe._config import sidecar_command
from vibe.core.types import LLMMessage, Role

_HOME = os.environ.get("ACCORDION_E2E_REPO", "")

pytestmark = [
    pytest.mark.skipif(not _HOME, reason="ACCORDION_E2E_REPO not set"),
    pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH"),
    pytest.mark.timeout(60),
]


def test_a_real_sidecar_completes_the_whole_handshake():
    home = Path(_HOME)
    assert sidecar_command(home) is not None, "no built sidecar bundle"

    bridge = AccordionBridge(
        home,
        session_id="e2e-1",
        cwd=Path.cwd(),
        harness_version="2.24.3",
        model={"id": "m", "provider": "mistral", "contextWindow": 128_000},
    )
    try:
        assert bridge.ensure_started(timeout=15.0) is True
        assert {s["name"] for s in bridge.tool_specs()} == {"unfold", "recall"}
        assert [s["name"] for s in bridge.command_specs()] == ["accordion"]

        messages = [
            LLMMessage(role=Role.system, content="SYSTEM"),
            LLMMessage(role=Role.user, content="hello"),
            LLMMessage(role=Role.assistant, content="hi " + "x" * 4000),
            LLMMessage(role=Role.user, content="again"),
        ]
        bridge.session_start("start", messages)
        assert bridge.skill_paths, "resources_discover returned no skill paths"

        out = bridge.context(messages, {"id": "m", "contextWindow": 128_000})
        # Folding is off at birth, so the sidecar answers null (passthrough).
        assert out is None
        assert bridge.context_passthroughs == 0, "null is not a failed passthrough"

        content, is_error = bridge.call_tool("recall", {"codes": ["zzz"]}, "call-1")
        assert is_error is False
        assert content

        ok, error = bridge.run_command("accordion", "")
        assert ok is True and error is None
        assert bridge.drain_notices(), "the command produced no notify"
    finally:
        bridge.close()
    assert bridge.closed


@pytest.mark.asyncio
async def test_a_real_sidecar_drives_a_full_agent_loop_turn(
    monkeypatch: pytest.MonkeyPatch, make_loop
):
    from accordion_vibe._loop import AccordionAgentLoop
    from tests.mock.utils import mock_llm_chunk
    from tests.stubs.fake_backend import FakeBackend

    monkeypatch.setenv("ACCORDION_REPO", _HOME)
    backend = FakeBackend([mock_llm_chunk(content="Response")])
    loop = make_loop(backend=backend)
    assert isinstance(loop, AccordionAgentLoop)

    events = [event async for event in loop.act("Hello")]

    assert events
    # Folding is off at birth, so the wire comes back untouched -- but it did
    # make the round trip, which is what proves the hot path survives a real
    # sidecar rather than only the fake one.
    sent = backend.requests_messages[0]
    assert [m.content for m in sent if m.role == Role.user] == ["Hello"]
    bridge = loop._accordion
    assert bridge is not None and bridge.attached
    assert bridge.context_passthroughs == 0
    assert "unfold" in loop.tool_manager.available_tools
    assert any("accordion" in name for name in loop.skill_manager.available_skills)
