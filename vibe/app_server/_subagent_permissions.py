"""Decide a declared subagent's calls under its profile's rules and its session's.

A profile narrows a tool per call: ``[tools.read_file] denylist`` refuses some
paths, ``[tools.bash] allowlist`` runs some commands unattended, ``permission =
"ask"`` prompts for a tool the session runs freely. The Runtime's ceiling holds
one allow/ask/deny per tool, so it cannot carry any of that. The per-call half is
answered here, with the same resolver the session uses, read against the
profile's configuration.

The session's own answer still applies. A profile is a point inside the session's
envelope, as its ceiling is: it can refuse or prompt for what the session allows,
never allow what the session refuses or prompts for. Profiles can come from
plugins, and none of them is a grant.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from mistralai_vibe_local_harness.vibe._permissions import (
    PermissionOutcome,
    PermissionResolver,
)
from vibe.app_server._unified_permissions import UnifiedPermissionResolver
from vibe.core.agents.registry import apply_profile_overrides
from vibe.core.config import VibeConfigSchema
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.tools.manager import ToolManager

type ToolsFactory = Callable[[Callable[[], VibeConfigSchema]], ToolManager]


def narrower(
    session: PermissionOutcome, profile: PermissionOutcome
) -> PermissionOutcome:
    """The stricter of two answers about one call.

    A refusal beats a prompt and a prompt beats a pass. Two prompts become one
    asking for both scopes, so the user's answer covers both sides. A prompt for
    the whole tool already covers any scope, so it is kept as is.
    """
    match session.decision, profile.decision:
        case ("deny", _) | ("ask", "allow"):
            return session
        case (_, "deny") | ("allow", _):
            return profile
        case _:
            return _both_prompts(session, profile)


def _both_prompts(
    session: PermissionOutcome, profile: PermissionOutcome
) -> PermissionOutcome:
    if not session.required_permissions:
        return session
    if not profile.required_permissions:
        return profile
    extra = tuple(
        required
        for required in profile.required_permissions
        if required not in session.required_permissions
    )
    return replace(
        session, required_permissions=(*session.required_permissions, *extra)
    )


class SubagentPermissions:
    """Builds the per-call resolver each declared subagent profile runs under."""

    def __init__(
        self,
        session: UnifiedPermissionResolver,
        config: ConfigOrchestrator[VibeConfigSchema],
        tools_for: ToolsFactory,
    ) -> None:
        self._session = session
        self._config = config
        self._tools_for = tools_for

    def resolver_for(self, overrides: Mapping[str, Any]) -> PermissionResolver:
        # Built on the first call rather than here: every bind resolves every
        # declared profile, and most of them never spawn a child. Rebuilt once
        # the session's configuration is replaced (a reload, a config write), so
        # a running child's next call follows the rules the session now has.
        built_from: VibeConfigSchema | None = None
        profile: UnifiedPermissionResolver | None = None
        lock = asyncio.Lock()

        async def resolve(
            builtin: str, arguments: Mapping[str, Any]
        ) -> PermissionOutcome:
            nonlocal built_from, profile
            outcome = await self._session.resolve(builtin, arguments)
            if outcome.decision == "deny":
                return outcome
            async with lock:
                current = self._config.config
                if profile is None or built_from is not current:
                    profile = await asyncio.to_thread(self._profile, overrides)
                    built_from = current
            return narrower(outcome, await profile.resolve(builtin, arguments))

        return resolve

    def _profile(self, overrides: Mapping[str, Any]) -> UnifiedPermissionResolver:
        config = self._config.copy()
        apply_profile_overrides(config, dict(overrides))
        return self._session.under(self._tools_for(lambda: config.config), config)


__all__ = ["SubagentPermissions", "narrower"]
