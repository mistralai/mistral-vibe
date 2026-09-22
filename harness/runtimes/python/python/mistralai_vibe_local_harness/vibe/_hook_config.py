"""Compile user hook declarations into Core bindings and Runtime handlers.

A hook lives in two halves: the Core carries the binding (point, order, selector) in
its per-session capability set and decides *when* to dispatch; the Runtime holds the
handler that actually runs the command. The shared binding id is what joins them, so
the Core's selection resolves to the right handler -- on resume too, from a checkpoint
the Core wrote. Bindings go to ``Host.start(hook_bindings=...)``, handlers to
``Host.configure_hook_handlers(...)``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from mistralai_vibe_local_harness.protocol import (
    RustHarnessHookBinding,
    RustHookPoint,
)
from mistralai_vibe_local_harness.vibe._foreign_hooks import (
    build_post_agent_turn_handler,
    build_post_tool_call_handler,
    build_pre_tool_call_handler,
)
from mistralai_vibe_local_harness.vibe._hook_matcher import binding_id, compile_selector
from mistralai_vibe_local_harness.vibe._local_actions import (
    HookHandlers,
    PostAgentTurnHookHandler,
    PostToolCallHookHandler,
    PreToolCallHookHandler,
)

# The authorable point names (what a user writes) and their Core hook points.
_AUTHORABLE_POINTS: dict[str, RustHookPoint] = {
    "pre_tool": "pre_tool_call",
    "post_tool": "post_tool_call",
    "post_agent": "post_agent_turn",
}

_DEFAULT_TIMEOUT_S = 60.0

__all__ = [
    "CompiledHooks",
    "ForeignHookDefinition",
    "compile_foreign_hooks",
]


@dataclass(frozen=True, slots=True)
class ForeignHookDefinition:
    # ``point`` is an authorable name, a key of ``_AUTHORABLE_POINTS``. ``source`` labels
    # the declaring file: a project-root path, or ``user`` for ``~/.vibe``. ``match`` is
    # a tool selector and must be ``None`` for ``post_agent``.
    name: str
    point: str
    command: str
    source: str = "user"
    match: str | None = None
    order: int = 0
    timeout_s: float = _DEFAULT_TIMEOUT_S
    strict: bool = False


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
    fires: a tool-scoped ``match`` binds ``always`` and is applied at call time inside
    the handler (see :mod:`._hook_matcher`).

    ``tool_catalog`` is accepted and never called. The Vibe app-server still passes the
    catalogue provider this used to need, because ``vibe/uv.lock`` can resolve a
    *released* Harness where the parameter is still required; dropping it there before
    that lock names a release containing call-time matching would break the CLI against
    the registry wheel. Drop the parameter once it does.
    """
    del tool_catalog
    bindings: list[RustHarnessHookBinding] = []
    pre_tool: dict[str, PreToolCallHookHandler] = {}
    post_tool: dict[str, PostToolCallHookHandler] = {}
    post_agent: dict[str, PostAgentTurnHookHandler] = {}
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

        if core_point == "pre_tool_call":
            pre_tool[bid] = build_pre_tool_call_handler(
                name=definition.name,
                command=definition.command,
                match=definition.match,
                timeout_s=definition.timeout_s,
                strict=definition.strict,
            )
        elif core_point == "post_tool_call":
            post_tool[bid] = build_post_tool_call_handler(
                name=definition.name,
                command=definition.command,
                match=definition.match,
                timeout_s=definition.timeout_s,
                strict=definition.strict,
            )
        else:  # post_agent_turn
            post_agent[bid] = build_post_agent_turn_handler(
                name=definition.name,
                command=definition.command,
                timeout_s=definition.timeout_s,
                strict=definition.strict,
            )

    return CompiledHooks(
        bindings=tuple(bindings),
        handlers=HookHandlers(
            pre_tool_call=pre_tool,
            post_tool_call=post_tool,
            post_agent_turn=post_agent,
        ),
    )
