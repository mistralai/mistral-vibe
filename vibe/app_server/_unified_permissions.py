"""Scope a Unified Harness approval to the call rather than the tool.

The Runtime gates builtin tools with ``tool_modes``: one ``allow``/``ask``/
``deny`` per builtin name. That is the right grain for "may this session run
commands at all" and the wrong grain for "may it run *this* command" —
``file_system.bash`` is one mode covering ``npm test`` and ``rm -rf /`` alike,
so a grant recorded against it is a grant against every command.

Vibe already answers the finer question for the legacy backend: every tool has
a ``resolve_permission`` that turns one call into the
:class:`~vibe.permissions.RequiredPermission` values it needs, and
:class:`PermissionStore` remembers which of those the user granted. This module
binds that machinery to the Runtime's resolver seam so both backends decide
approvals the same way, off the same code.

The translation each builtin needs is only its permission-relevant arguments:
the path a file tool touches, the command a shell runs. Forwarding the rest
would mean reconciling two argument schemas that already disagree (the Runtime
reads from offset 0, Vibe's ``read_file`` from line 1) to feed fields no
permission rule reads.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, cast

from mistralai_vibe_local_harness.protocol import (  # pyright: ignore[reportMissingImports]
    RustRuntimeBuiltinToolName,
)
from mistralai_vibe_local_harness.vibe._permissions import (  # pyright: ignore[reportMissingImports]
    PermissionOutcome,
)
from pydantic import BaseModel

from vibe.core.config import VibeConfigSchema
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.tools.base import BaseTool
from vibe.core.tools.manager import NoSuchToolError, ToolManager
from vibe.core.tools.models import (
    ApprovedRule,
    PermissionContext,
    PermissionScope,
    ToolPermission,
)
from vibe.core.tools.permissions import PermissionStore, wildcard_match
from vibe.permissions import RequiredPermission

# The Rust Runtime's builtin tools and Vibe's tool catalogue are separate
# namespaces; this is the only overlap the local adapter can currently execute.
RUST_BUILTIN_TOOL_SOURCES: dict[RustRuntimeBuiltinToolName, frozenset[str]] = {
    "file_system.read_file": frozenset({"read_file"}),
    "file_system.write_file": frozenset({"write_file"}),
    "file_system.search_replace": frozenset({"edit"}),
    "file_system.bash": frozenset({"bash", "powershell", "git_bash"}),
    "skill.read": frozenset({"skill"}),
}


def _first_block(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    content = arguments.get("content")
    if isinstance(content, list) and content and isinstance(content[0], dict):
        return content[0]
    return {}


def _search_replace_args(arguments: Mapping[str, Any]) -> dict[str, Any]:
    block = _first_block(arguments)
    return {
        "file_path": arguments.get("file_path"),
        "old_string": block.get("old_str", ""),
        "new_string": block.get("new_str", ""),
        "replace_all": block.get("replace_all", False),
    }


# Rust argument names on the left of each mapping, Vibe's on the right. Only
# what a permission rule reads, plus whatever the Vibe args model requires to
# validate at all.
_ARGUMENT_TRANSLATORS: dict[
    RustRuntimeBuiltinToolName, Callable[[Mapping[str, Any]], dict[str, Any]]
] = {
    "file_system.read_file": lambda args: {"file_path": args.get("path")},
    "file_system.write_file": lambda args: {
        "file_path": args.get("path"),
        "content": args.get("content", ""),
    },
    "file_system.search_replace": _search_replace_args,
    "file_system.bash": lambda args: {"command": args.get("command", "")},
    "skill.read": lambda args: {"name": args.get("name", "")},
}

_OUTCOME_BY_PERMISSION: dict[ToolPermission, Literal["allow", "deny"]] = {
    ToolPermission.ALWAYS: "allow",
    ToolPermission.NEVER: "deny",
}


def _shell_command_scope(arguments: Mapping[str, Any]) -> str | None:
    """The command as written -- blank included, since blank is still exact.

    A blank or whitespace-only command is as unreadable to a shell's splitter as
    ``&&`` is, and scoping it to itself grants exactly the one call the user saw:
    the pattern matches that command and nothing else. Only arguments carrying no
    command at all have nothing to scope, and those are arguments the shell tool
    will itself reject.
    """
    command = arguments.get("command")
    if not isinstance(command, str):
        return None
    return command


# A shell call is never really unscopeable. A shell returns nothing to scope
# when it cannot split the command into parts -- and the two Windows shells use
# different splitters, so either can come back empty on a command the other
# reads fine. The raw command is still an exact scope, and it is the only thing
# narrower than "every command" that a grant can be recorded against: the
# alternative is ``ToolPermission.ALWAYS`` on the shell tool, which
# ``_is_unconditionally_allowed`` reads as "allow everything from here on".
_FALLBACK_SCOPES: dict[
    RustRuntimeBuiltinToolName, Callable[[Mapping[str, Any]], str | None]
] = {"file_system.bash": _shell_command_scope}


class UnifiedPermissionResolver:
    """Answers the Runtime's ``ask`` with Vibe's per-call permission rules.

    Owns the session's :class:`PermissionStore`, so it must outlive every
    derivation: the Host rebuilds ``LocalRuntimeAdapterConfig`` on each config
    change, and a store rebuilt with it would forget grants mid-session and
    re-prompt for what the user already approved.
    """

    def __init__(
        self,
        tools: ToolManager,
        store: PermissionStore,
        config: ConfigOrchestrator[VibeConfigSchema],
    ) -> None:
        self._tools = tools
        self._store = store
        self._config = config

    @property
    def store(self) -> PermissionStore:
        return self._store

    def vibe_tool_names(self, builtin: str) -> tuple[str, ...]:
        sources = RUST_BUILTIN_TOOL_SOURCES.get(
            cast(RustRuntimeBuiltinToolName, builtin), frozenset()
        )
        available = self._tools.available_tools
        return tuple(sorted(name for name in sources if name in available))

    async def resolve(
        self, builtin: str, arguments: Mapping[str, Any]
    ) -> PermissionOutcome:
        """Decide one call the way ``AgentLoop._should_execute_tool`` would.

        ``file_system.bash`` stands for every shell the catalogue offers, and on
        Windows that is two at once. The shells parse a command separately, so
        one can scope it while the other has no opinion on it at all. Whatever
        any of them scopes has to survive to the prompt: an ask that carries no
        permissions is one ``grant`` records against the tools themselves, which
        would turn approving ``npm test`` into approving every command. When no
        shell could read the command at all, the command itself becomes the
        scope, so that grant stays narrow too.
        """
        names = self.vibe_tool_names(builtin)
        if not names:
            # The mode already denies a builtin with no catalogue entry; leaving
            # it to ask would be the Runtime's own answer either way.
            return PermissionOutcome(decision="ask")

        # Every question below reads the same translated arguments; validate once.
        resolved = {
            name: self._tool_and_args(name, builtin, arguments) for name in names
        }
        authorized_path = self._resolve_path_for(resolved)

        uncovered: list[RequiredPermission] = []
        scopeable = False
        unscoped = False
        allowed = False
        async with self._store.lock:
            for name in names:
                context = self._context_for(name, resolved[name])
                fixed = _OUTCOME_BY_PERMISSION.get(context.permission)
                if fixed == "deny":
                    return PermissionOutcome(decision="deny", reason=context.reason)
                if fixed == "allow":
                    allowed = True
                    continue
                if not context.required_permissions:
                    # This tool has nothing to say about this call. Record that
                    # and keep going: a sibling that does scope the call answers
                    # for the builtin, and letting this one force the unscoped
                    # branch below would discard that scope.
                    unscoped = True
                    continue
                scopeable = True
                uncovered.extend(
                    rp
                    for rp in context.required_permissions
                    if not self._store.covers(name, rp)
                )

            if unscoped and not scopeable and not allowed:
                # `allowed` keeps a tool that positively cleared this call out of
                # here. A tool that abstains is not a tool that asks, and letting
                # it through would offer a prompt granting more than the call a
                # sibling had already cleared.
                fallback = self._fallback_permission(builtin, arguments)
                if fallback is None:
                    # Nothing anywhere in the builtin to scope a grant to and no
                    # argument to fall back on, so the tool itself is the only
                    # thing that can be granted. Legacy says the same.
                    return PermissionOutcome(
                        decision="ask", authorized_path=authorized_path
                    )
                if any(not self._covered(name, fallback) for name in names):
                    uncovered.append(fallback)

        if uncovered:
            return PermissionOutcome(
                decision="ask",
                required_permissions=tuple(
                    rp.model_dump(mode="json", by_alias=True) for rp in uncovered
                ),
                authorized_path=authorized_path,
            )
        return PermissionOutcome(decision="allow", authorized_path=authorized_path)

    def _fallback_permission(
        self, builtin: str, arguments: Mapping[str, Any]
    ) -> RequiredPermission | None:
        """A scope for a call no tool could read, so the grant is not tool-wide."""
        build = _FALLBACK_SCOPES.get(cast(RustRuntimeBuiltinToolName, builtin))
        if build is None:
            return None
        command = build(arguments)
        if command is None:
            return None
        return RequiredPermission(
            scope=PermissionScope.COMMAND_PATTERN,
            invocation_pattern=command,
            session_pattern=command,
            label=command,
        )

    def _covered(self, name: str, fallback: RequiredPermission) -> bool:
        """Whether this call was already granted, for the session or for good.

        The tools match their own allowlists only against a parsed command, and
        a call reaches the fallback precisely because parsing produced nothing.
        So the resolver has to read back the entry it persisted itself, or
        "always" would prompt again in every later session.
        """
        if self._store.covers(name, fallback):
            return True
        return any(
            wildcard_match(fallback.invocation_pattern, pattern)
            for pattern in self._tools.get_tool_config(name).allowlist
        )

    def _tool_and_args(
        self, name: str, builtin: str, arguments: Mapping[str, Any]
    ) -> tuple[BaseTool, BaseModel] | None:
        """The Vibe tool for this builtin, and the call in its own argument shape."""
        translate = _ARGUMENT_TRANSLATORS.get(cast(RustRuntimeBuiltinToolName, builtin))
        if translate is None:
            return None
        try:
            tool = self._tools.get(name)
            return tool, type(tool).validate_arguments(translate(arguments))
        except (NoSuchToolError, ValueError):
            # Arguments the tool would itself reject. Its own validation will
            # fail the call; deciding permission off a guess would be worse.
            return None

    @staticmethod
    def _resolve_path_for(
        resolved: Mapping[str, tuple[BaseTool, BaseModel] | None],
    ) -> Path | None:
        """Resolve this call's path, so the file tool acts on the same one.

        The outcome carries it to the harness, so both agree on the path —
        including an approved path outside the workspace roots.
        """
        for pair in resolved.values():
            if pair is None:
                continue
            tool, args = pair
            raw = getattr(args, "file_path", None)
            if raw is None:
                continue
            try:
                path = Path(str(raw)).expanduser()
                if not path.is_absolute():
                    path = tool.cwd / path
                return path.resolve()
            except (ValueError, OSError):
                return None
        return None

    def _context_for(
        self, name: str, pair: tuple[BaseTool, BaseModel] | None
    ) -> PermissionContext:
        """One Vibe tool's verdict on this call, falling back to its config."""
        configured = PermissionContext(
            permission=self._tools.get_tool_config(name).permission
        )
        if pair is None:
            return configured
        tool, args = pair
        return tool.resolve_permission(args) or configured

    async def grant(
        self,
        builtin: str,
        required_permissions: Sequence[RequiredPermission],
        *,
        permanent: bool,
    ) -> None:
        """Record what the user approved, scoped the way legacy scopes it."""
        if not required_permissions and builtin in _FALLBACK_SCOPES:
            # A builtin that can always scope a call it can read has no honest
            # tool-wide grant. What still arrives here with nothing attached is a
            # call no resolver could read at all -- arguments the tool will itself
            # reject, or a resolver that raised and left the Runtime asking on its
            # own ``ALWAYS_ASK``, which carries no permissions either. Recording
            # ``ToolPermission.ALWAYS`` off one of those would buy "allow every
            # command" from a call that ran none, and persist it to the user's
            # config if they answered "always". Remember nothing instead: the
            # answer releases the call it was asked about, and only that one.
            return
        for name in self.vibe_tool_names(builtin):
            if required_permissions:
                for rp in required_permissions:
                    self._store.add_rule(
                        ApprovedRule(
                            tool_name=name,
                            scope=rp.scope,
                            session_pattern=rp.session_pattern,
                        )
                    )
            else:
                self._store.set_tool_permission(name, ToolPermission.ALWAYS)
            if permanent:
                await self._persist(name, required_permissions)

    async def _persist(
        self, name: str, required_permissions: Sequence[RequiredPermission]
    ) -> None:
        """Write an "always" grant to the config layer that outlives the session."""
        if not required_permissions:
            await self._config.set_field(
                f"/tools/{name}/permission", ToolPermission.ALWAYS.value
            )
            return
        patterns = [
            rp.session_pattern
            for rp in required_permissions
            if rp.session_pattern.strip()
        ]
        if not patterns:
            # A blank command scopes a session grant exactly, but there is
            # nothing worth writing down about a call that runs nothing -- and
            # the shells match an allowlist by prefix, where a blank entry is
            # junk that matches the empty command and anything the tool ever
            # hands it with a leading space. The session store already covers
            # this call; the next session can ask again for free.
            return
        update = self._config.config.build_tool_allowlist_update(
            name,
            patterns,
            current_allowlist=self._tools.get_tool_config(name).allowlist,
        )
        if update is None:
            return
        await self._config.set_field(
            f"/tools/{name}/allowlist", update["tools"][name]["allowlist"]
        )


__all__ = ["RUST_BUILTIN_TOOL_SOURCES", "UnifiedPermissionResolver"]
