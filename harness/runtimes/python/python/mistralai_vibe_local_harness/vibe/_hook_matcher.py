"""Compile ``hooks.toml`` ``match`` expressions into hook selectors and predicates.

Matching happens at **call time**, against the tool actually being invoked, the way the
legacy runtime does it (`vibe/core/hooks/_pre_tool.py`). Resolving at compile time
instead, against a ``hook_tool_catalog`` snapshot, produced an empty selector -- a hook
that silently never fires -- whenever the snapshot disagreed with the user's spelling or
with the eventual capability set: the catalogue is keyed by runtime name so
``match = "edit"`` selected nothing, and MCP/connector groups merge in only later, at
bind time.

Matching here decides whether a *dispatched* hook runs its command; whether the Core
dispatches is the Core's own decision (``tools/external.rs``).

The glob dialect is :mod:`fnmatch`, case-insensitive and anchored, so ``*``, ``?`` and
``[...]`` behave as they do in legacy. ``re:<regex>`` selects the regex dialect instead.

A ``post_agent`` hook's ``match`` uses the same dialect against the agent that finished
its turn: its agent configuration name and, for a main session (a root session or a
fork), the ``main`` alias. See :func:`agent_candidate_names`.
"""

from __future__ import annotations

from collections.abc import Callable
from fnmatch import fnmatch
import re

from mistralai_vibe_local_harness.protocol import (
    RustAlwaysHookSelector,
    RustExternalToolCall,
    RustHarnessHookSelector,
    RustHarnessHookToolKey,
    RustHookPoint,
    RustProvidedToolCall,
)

# The Core rejects a ``tool_keys`` selector at these points, so they cannot be scoped
# by tool. ``post_agent_turn`` is the only lifecycle point that takes a ``match``: it
# selects the agent that finished its turn, applied by the Runtime at call time.
_UNMATCHABLE_LIFECYCLE_POINTS: frozenset[RustHookPoint] = frozenset({
    "pre_agent_turn",
    "pre_llm_call",
    "post_llm_call",
})

_MATCH_ALL = "*"
_REGEX_PREFIX = "re:"

# The alias every main session (a root session or a fork) answers to, whatever agent
# it runs, so a post_agent hook can target the user's agent but not its subagents.
MAIN_AGENT_ALIAS = "main"

# What the model calls each builtin -- the name a user reads off the tool list and writes
# in ``match``, which is often neither the qualified name nor its leaf. The authority is
# ``core/src/core/tools/external.rs`` (``direct_model_name`` / ``programmatic_name``).
#
# Listed exhaustively, including the names that are already the leaf, so the table reads
# as a checklist: ``test_builtin_name_table_covers_the_core_tool_catalogue`` pins its keys
# against the native catalogue, and a new builtin fails that test until someone writes
# down what the model calls it.
_BUILTIN_MODEL_NAMES: dict[str, tuple[str, ...]] = {
    "self.wait": ("wait",),
    "self.sleep": ("sleep",),
    "file_system.read_file": ("read_file",),
    "file_system.write_file": ("write_file",),
    "file_system.edit": ("edit",),
    "file_system.bash": ("bash",),
    "skill.read": ("skill",),
    "process.start": ("process_start",),
    "process.output": ("process_output",),
    "process.write": ("process_write",),
    "process.list": ("process_list",),
    "process.stop": ("process_stop",),
    "agents.list_configs": ("agents_list_configs",),
    "agents.list": ("agents_list",),
    "agents.spawn": ("agents_spawn",),
    "agents.send_message": ("agents_send_message",),
    "agents.interrupt": ("agents_interrupt",),
    # Deprecated: retired `subagent.*` spellings, kept only so hooks still match
    # replayed calls. Use the `agents.*` entries above.
    "subagent.list": ("agent_list",),
    "subagent.spawn": ("agent_spawn",),
    "subagent.wait": ("agent_wait",),
    "subagent.send_message": ("agent_message",),
    "subagent.interrupt": ("agent_interrupt",),
    "subagent.stop": ("agent_stop",),
}

__all__ = [
    "MAIN_AGENT_ALIAS",
    "agent_candidate_names",
    "binding_id",
    "candidate_names",
    "compile_selector",
    "is_unscoped",
    "match_predicate",
    "qualified_tool_name",
    "selects_agent",
    "selects_tool",
    "tool_catalog_for_config",
]


def qualified_tool_name(call: RustExternalToolCall) -> str:
    if isinstance(call, RustProvidedToolCall):
        return f"{call.group_name}.{call.tool_name}"
    return call.name


def match_predicate(match: str | None) -> Callable[[str], bool]:
    if match is None or match == _MATCH_ALL:
        return lambda _name: True

    if match.startswith(_REGEX_PREFIX):
        try:
            compiled = re.compile(match[len(_REGEX_PREFIX) :], re.IGNORECASE)
        except re.error:
            # The CLI reports the bad pattern; a hook that matches nothing beats one that
            # raises on every tool call.
            return lambda _name: False
        return lambda name: compiled.fullmatch(name) is not None

    pattern = match.lower()
    return lambda name: fnmatch(name.lower(), pattern)


def candidate_names(qualified_name: str) -> tuple[str, ...]:
    leaf = qualified_name.rsplit(".", 1)[-1]
    # The underscore-joined form is what legacy published MCP tools under.
    names = {qualified_name, leaf, qualified_name.replace(".", "_")}
    names.update(_BUILTIN_MODEL_NAMES.get(qualified_name, ()))
    return tuple(names)


def selects_tool(predicate: Callable[[str], bool], qualified_name: str) -> bool:
    return any(predicate(name) for name in candidate_names(qualified_name))


def is_unscoped(match: str | None) -> bool:
    return match is None or match == _MATCH_ALL


def agent_candidate_names(name: str | None, *, main: bool) -> tuple[str, ...]:
    # A subagent configuration spelled like the alias must not pass for the main agent.
    names = (
        ()
        if name is None or (not main and name.casefold() == MAIN_AGENT_ALIAS)
        else (name,)
    )
    return (*names, MAIN_AGENT_ALIAS) if main else names


def selects_agent(match: str | None, name: str | None, *, main: bool) -> bool:
    """Whether a post_agent ``match`` selects the agent named ``name``.

    An unscoped hook (no ``match``, or ``*``) selects every agent, including one whose
    name is unknown; any other pattern needs a candidate name to match.
    """
    if is_unscoped(match):
        return True
    predicate = match_predicate(match)
    return any(
        predicate(candidate) for candidate in agent_candidate_names(name, main=main)
    )


def compile_selector(
    point: RustHookPoint, match: str | None
) -> RustHarnessHookSelector:
    if point in _UNMATCHABLE_LIFECYCLE_POINTS and match is not None:
        raise ValueError(f"lifecycle hook point {point!r} cannot declare a match")
    return RustAlwaysHookSelector()


def tool_catalog_for_config(config_json: str) -> list[RustHarnessHookToolKey]:
    """Fetch and parse the Core's tool catalogue for a config (native call).

    Nothing in this package calls it. It stays exported for the Vibe app-server, which
    still passes its result to :func:`compile_foreign_hooks` because ``vibe/uv.lock`` can
    resolve a *released* Harness that still requires it. Remove both once that lock names
    a release containing call-time matching.
    """
    import json

    from mistralai_vibe_local_harness import hook_tool_catalog

    return [
        RustHarnessHookToolKey.model_validate(item)
        for item in json.loads(hook_tool_catalog(config_json))
    ]


def binding_id(source: str, name: str) -> str:
    # Deliberately excludes execution order: a session's Core carries frozen bindings and
    # re-attaches handlers by id on resume, so reordering ``hooks.toml`` must not change
    # an existing hook's id.
    return f"{source}:{name}"
