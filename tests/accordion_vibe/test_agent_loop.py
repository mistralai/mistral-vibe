"""End to end through a real ``AgentLoop``, a real sidecar process, a fake backend."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from accordion_vibe import _bridge as bridge_module
from accordion_vibe._bridge import AccordionBridge, active_bridges, primary_bridge
from accordion_vibe._loop import AccordionAgentLoop
from accordion_vibe.tools.accordion_tools import RecallArgs, UnfoldArgs
from tests.accordion_vibe.conftest import HANDSHAKE_TIMEOUT_S, Sidecar, wait_until
from tests.accordion_vibe.fake_sidecar import FOLD_PREFIX
from tests.mock.utils import collect_result, mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from tests.stubs.fake_mcp_registry import FakeMCPRegistry
from vibe.core.agent_loop import AgentLoop
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import VibeConfigSchema
from vibe.core.middleware import AutoCompactMiddleware
from vibe.core.tools.base import InvokeContext
from vibe.core.types import LLMMessage, Role


@pytest.fixture
def make_bridged_loop(
    monkeypatch: pytest.MonkeyPatch,
    accordion_repo: Path,
    patch_sidecar: Callable[[Sidecar], None],
    make_loop: Callable[..., AgentLoop],
) -> Callable[..., AccordionAgentLoop]:
    """Build a loop that ``build_agent_loop`` resolves to the Accordion subclass."""

    def _make(sidecar: Sidecar, **kwargs: Any) -> AccordionAgentLoop:
        patch_sidecar(sidecar)
        monkeypatch.setenv("ACCORDION_REPO", str(accordion_repo))
        loop = make_loop(**kwargs)
        assert isinstance(loop, AccordionAgentLoop)
        return loop

    return _make


def _attached(loop: AccordionAgentLoop) -> AccordionBridge:
    bridge = primary_bridge()
    assert bridge is not None
    assert bridge.wait_ready(HANDSHAKE_TIMEOUT_S), "sidecar handshake never landed"
    assert bridge.session_id == loop.session_id
    return bridge


def _wire(backend: FakeBackend) -> list[tuple[str, Any]]:
    return [(m.role.value, m.content) for m in backend.requests_messages[0]]


def _one_reply() -> FakeBackend:
    return FakeBackend([mock_llm_chunk(content="Response")])


# -- inert ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_bridge_is_inert_without_an_accordion_repo(
    monkeypatch: pytest.MonkeyPatch,
    vibe_config: VibeConfigSchema,
    make_loop: Callable[..., AgentLoop],
):
    def _never(_home: Path) -> None:
        raise AssertionError("an inert bridge must never look for a sidecar")

    monkeypatch.setattr(bridge_module, "sidecar_command", _never)
    bridged_backend = _one_reply()
    loop = make_loop(backend=bridged_backend)

    assert type(loop) is AgentLoop
    assert active_bridges() == []

    [_ async for _ in loop.act("Hello")]

    reference_backend = _one_reply()
    reference = AgentLoop(
        config_orchestrator=FakeConfigOrchestrator(vibe_config),
        agent_name=BuiltinAgentName.ASK,
        backend=reference_backend,
        enable_streaming=False,
        mcp_registry=FakeMCPRegistry(),
    )
    try:
        [_ async for _ in reference.act("Hello")]
    finally:
        await reference.aclose()

    assert _wire(bridged_backend) == _wire(reference_backend)


# -- the hot path -----------------------------------------------------------


@pytest.mark.asyncio
async def test_the_sidecars_rewrite_reaches_the_backend(
    make_sidecar: Callable[..., Sidecar],
    make_bridged_loop: Callable[..., AccordionAgentLoop],
):
    sidecar = make_sidecar(context="replace")
    backend = _one_reply()
    loop = make_bridged_loop(sidecar, backend=backend)

    [_ async for _ in loop.act("Hello")]

    contents = [content for _role, content in _wire(backend)]
    folded = [c for c in contents if isinstance(c, str) and c.startswith(FOLD_PREFIX)]
    assert folded, f"no rewritten message reached the backend: {contents}"
    assert any("Hello" in c for c in folded)
    # The loop's own history is untouched: folding only ever edits the wire.
    assert not any(
        isinstance(m.content, str) and m.content.startswith(FOLD_PREFIX)
        for m in loop.messages
    )
    assert sidecar.received_of("context")


@pytest.mark.asyncio
async def test_a_context_timeout_passes_the_messages_through_unmodified(
    make_sidecar: Callable[..., Sidecar],
    make_bridged_loop: Callable[..., AccordionAgentLoop],
):
    backend = _one_reply()
    loop = make_bridged_loop(make_sidecar(context="hang"), backend=backend)

    [_ async for _ in loop.act("Hello")]

    bridge = _attached(loop)
    assert bridge.context_passthroughs >= 1
    assert not any(
        isinstance(c, str) and c.startswith(FOLD_PREFIX) for _r, c in _wire(backend)
    )
    assert any("Hello" in (c or "") for _r, c in _wire(backend))


@pytest.mark.asyncio
async def test_a_crashed_sidecar_passes_through_without_raising(
    make_sidecar: Callable[..., Sidecar],
    make_bridged_loop: Callable[..., AccordionAgentLoop],
):
    backend = _one_reply()
    loop = make_bridged_loop(
        make_sidecar(context="replace", crash_on="context"), backend=backend
    )
    bridge = _attached(loop)

    [_ async for _ in loop.act("Hello")]

    assert wait_until(lambda: not bridge.attached)
    assert not any(
        isinstance(c, str) and c.startswith(FOLD_PREFIX) for _r, c in _wire(backend)
    )
    assert any("Hello" in (c or "") for _r, c in _wire(backend))


@pytest.mark.asyncio
async def test_only_the_main_completion_consults_the_sidecar(
    make_sidecar: Callable[..., Sidecar],
    make_bridged_loop: Callable[..., AccordionAgentLoop],
):
    loop = make_bridged_loop(make_sidecar(context="replace"), backend=_one_reply())
    [_ async for _ in loop.act("Hello")]
    model = loop.config.get_active_model()

    main = loop._messages_for_backend(loop.messages, model)
    synthetic = loop._messages_for_backend(
        [LLMMessage(role=Role.user, content="summarise this")], model
    )

    assert any(
        isinstance(m.content, str) and m.content.startswith(FOLD_PREFIX) for m in main
    )
    assert [m.content for m in synthetic] == ["summarise this"]


# -- folding toggle ---------------------------------------------------------


@pytest.mark.asyncio
async def test_autocompact_middleware_stays_while_folding_is_off(
    make_sidecar: Callable[..., Sidecar],
    make_bridged_loop: Callable[..., AccordionAgentLoop],
):
    loop = make_bridged_loop(make_sidecar())
    bridge = _attached(loop)

    loop._get_context()

    assert bridge.folding_enabled is False
    assert any(
        isinstance(m, AutoCompactMiddleware)
        for m in loop.middleware_pipeline.middlewares
    )


@pytest.mark.asyncio
async def test_folding_swaps_out_the_autocompact_middleware(
    make_sidecar: Callable[..., Sidecar],
    make_bridged_loop: Callable[..., AccordionAgentLoop],
):
    loop = make_bridged_loop(make_sidecar(folding_sequence=[True]))
    bridge = _attached(loop)
    assert wait_until(lambda: bridge.folding_enabled)

    loop._get_context()

    assert not any(
        isinstance(m, AutoCompactMiddleware)
        for m in loop.middleware_pipeline.middlewares
    )
    assert loop.middleware_pipeline.middlewares, "the rest of the pipeline survives"


# -- tools ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accordion_tools_exist_only_for_a_bridged_loop(
    make_sidecar: Callable[..., Sidecar],
    make_loop: Callable[..., AgentLoop],
    make_bridged_loop: Callable[..., AccordionAgentLoop],
):
    plain = make_loop()
    assert "unfold" not in plain.tool_manager.available_tools
    assert "recall" not in plain.tool_manager.available_tools

    bridged = make_bridged_loop(make_sidecar())

    assert {"unfold", "recall"} <= set(bridged.tool_manager.available_tools)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("unfold", UnfoldArgs(codes=["a1f", "9c2"])),
        ("recall", RecallArgs(codes=["3e"])),
    ],
)
async def test_the_accordion_tools_relay_to_the_sidecar(
    tool_name: str,
    args: Any,
    make_sidecar: Callable[..., Sidecar],
    make_bridged_loop: Callable[..., AccordionAgentLoop],
):
    sidecar = make_sidecar()
    loop = make_bridged_loop(sidecar)
    _attached(loop)

    tool = loop.tool_manager.get(tool_name)
    ctx = InvokeContext(tool_call_id="call-9", session_id=loop.session_id)
    result = await collect_result(tool.run(args, ctx))

    sent = sidecar.received_of("tool")[0]
    assert sent["name"] == tool_name
    assert sent["args"] == {"codes": list(args.codes)}
    assert sent["toolCallId"] == "call-9"
    assert f'"name": "{tool_name}"' in result.content
