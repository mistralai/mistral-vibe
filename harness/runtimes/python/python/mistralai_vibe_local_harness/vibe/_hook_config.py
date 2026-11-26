"""Compile user hook declarations into Core bindings and Runtime handlers.

A hook lives in two halves: the Core carries the binding (point, order, selector) in
its per-session capability set and decides *when* to dispatch; the Runtime holds the
handler that actually runs the command. The shared binding id is what joins them, so
the Core's selection resolves to the right handler -- on resume too, from a checkpoint
the Core wrote. Bindings go to ``Host.start(hook_bindings=...)``, handlers to
``Host.configure_hook_handlers(...)``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from mistralai_vibe_local_harness.protocol import RustHarnessHookBinding, RustHookPoint
from mistralai_vibe_local_harness.vibe._foreign_hooks import (
    HookProcess,
    HookProcessResolver,
    build_post_agent_turn_handler,
    build_post_llm_call_handler,
    build_post_tool_call_handler,
    build_pre_agent_turn_handler,
    build_pre_llm_call_handler,
    build_pre_tool_call_handler,
)
from mistralai_vibe_local_harness.vibe._hook_matcher import binding_id, compile_selector
from mistralai_vibe_local_harness.vibe._local_actions import (
    HookHandlers,
    PostAgentTurnHookHandler,
    PostLlmCallHookHandler,
    PostToolCallHookHandler,
    PreAgentTurnHookHandler,
    PreLlmCallHookHandler,
    PreToolCallHookHandler,
)

# The authorable point names (what a user writes) and their Core hook points.
_AUTHORABLE_POINTS: dict[str, RustHookPoint] = {
    "pre_tool": "pre_tool_call",
    "post_tool": "post_tool_call",
    "pre_agent": "pre_agent_turn",
    "pre_llm": "pre_llm_call",
    "post_llm": "post_llm_call",
    "post_agent": "post_agent_turn",
}

_DEFAULT_TIMEOUT_S = 60.0

__all__ = ["CompiledHooks", "ForeignHookDefinition", "compile_foreign_hooks"]


@dataclass(frozen=True, slots=True)
class ForeignHookDefinition:
    # ``point`` is an authorable name, a key of ``_AUTHORABLE_POINTS``. ``source`` labels
    # the declaring file: a project-root path, ``user`` for ``~/.vibe``, or a value
    # naming a plugin. ``match`` selects tools for ``pre_tool``/``post_tool`` and agents
    # for ``pre_agent``/``post_agent``. ``cwd`` and ``env`` default to the session's
    # workspace and environment; a plugin hook runs in its plugin's directory instead.
    # ``process``, when set, picks them per run from the session the hook runs in,
    # and wins over both.
    name: str
    point: str
    command: str
    source: str = "user"
    match: str | None = None
    order: int = 0
    timeout_s: float = _DEFAULT_TIMEOUT_S
    strict: bool = False
    cwd: Path | None = None
    env: Mapping[str, str] = field(default_factory=dict[str, str])
    process: HookProcessResolver | None = None


@dataclass(frozen=True, slots=True)
class CompiledHooks:
    bindings: tuple[RustHarnessHookBinding, ...] = ()
    handlers: HookHandlers = field(default_factory=HookHandlers)


def compile_foreign_hooks(
    definitions: Sequence[ForeignHookDefinition],
    *,
    tool_catalog: Callable[[], object] | None = None,
) -> CompiledHooks:
    """Compile ``definitions`` into shared Core bindings and Runtime handlers.

    Every declaration produces a binding, so no hook can compile to something that never
    fires: a ``match`` binds ``always`` and is applied at call time by the executor,
    against the called tool or, for a turn hook, the agent whose turn it is (see
    :mod:`._hook_matcher`).

    ``tool_catalog`` is accepted and never called. The Vibe app-server still passes the
    catalogue provider this used to need, because ``vibe/uv.lock`` can resolve a
    *released* Harness where the parameter is still required; dropping it there before
    that lock names a release containing call-time matching would break the CLI against
    the registry wheel. Drop the parameter once it does.
    """
    del tool_catalog
    bindings: list[RustHarnessHookBinding] = []
    handlers = _HandlerMaps()
    seen: set[str] = set()

    for definition in definitions:
        core_point = _AUTHORABLE_POINTS.get(definition.point)
        if core_point is None:
            raise ValueError(f"unknown authorable hook point {definition.point!r}")

        selector = compile_selector(core_point, definition.match)

        bid = binding_id(definition.source, definition.name)
        if bid in seen:
            raise ValueError(f"duplicate hook binding id {bid!r}")
        seen.add(bid)

        bindings.append(
            RustHarnessHookBinding(
                id=bid, point=core_point, order=definition.order, selector=selector
            )
        )

        handlers.add(core_point, bid, definition)

    return CompiledHooks(bindings=tuple(bindings), handlers=handlers.compiled())


@dataclass(slots=True)
class _HandlerMaps:
    pre_tool: dict[str, PreToolCallHookHandler] = field(default_factory=dict)
    post_tool: dict[str, PostToolCallHookHandler] = field(default_factory=dict)
    pre_agent: dict[str, PreAgentTurnHookHandler] = field(default_factory=dict)
    pre_llm: dict[str, PreLlmCallHookHandler] = field(default_factory=dict)
    post_llm: dict[str, PostLlmCallHookHandler] = field(default_factory=dict)
    post_agent: dict[str, PostAgentTurnHookHandler] = field(default_factory=dict)

    def add(
        self, point: RustHookPoint, bid: str, definition: ForeignHookDefinition
    ) -> None:
        name, command, match = definition.name, definition.command, definition.match
        timeout_s, strict = definition.timeout_s, definition.strict
        process = definition.process or HookProcess(
            cwd=definition.cwd, env=definition.env
        )
        match point:
            case "pre_tool_call":
                self.pre_tool[bid] = build_pre_tool_call_handler(
                    name=name, command=command, match=match,
                    timeout_s=timeout_s, strict=strict, process=process,
                )  # fmt: skip
            case "post_tool_call":
                self.post_tool[bid] = build_post_tool_call_handler(
                    name=name, command=command, match=match,
                    timeout_s=timeout_s, strict=strict, process=process,
                )  # fmt: skip
            case "pre_agent_turn":
                self.pre_agent[bid] = build_pre_agent_turn_handler(
                    name=name, command=command, match=match,
                    timeout_s=timeout_s, strict=strict, process=process,
                )  # fmt: skip
            case "pre_llm_call":
                self.pre_llm[bid] = build_pre_llm_call_handler(
                    name=name, command=command, match=match,
                    timeout_s=timeout_s, strict=strict, process=process,
                )  # fmt: skip
            case "post_llm_call":
                self.post_llm[bid] = build_post_llm_call_handler(
                    name=name, command=command, match=match,
                    timeout_s=timeout_s, strict=strict, process=process,
                )  # fmt: skip
            case "post_agent_turn":
                self.post_agent[bid] = build_post_agent_turn_handler(
                    name=name, command=command, match=match,
                    timeout_s=timeout_s, strict=strict, process=process,
                )  # fmt: skip
            case _:
                raise ValueError(f"hook point {point!r} has no foreign hook")

    def compiled(self) -> HookHandlers:
        return HookHandlers(
            pre_tool_call=self.pre_tool,
            pre_agent_turn=self.pre_agent,
            post_tool_call=self.post_tool,
            post_agent_turn=self.post_agent,
            pre_llm_call=self.pre_llm,
            post_llm_call=self.post_llm,
        )
