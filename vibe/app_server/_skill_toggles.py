"""Whether a skill is on, and whether a toggle may change that.

Sessions filter skills by name through ``enabled_skills`` and
``disabled_skills``; a toggle adds the name to, or removes it from, the
writable layer's ``disabled_skills``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from vibe.app_server._dispatch import RequestFailure
from vibe.app_server.protocol import ProtocolErrorCode
from vibe.core.config import VibeConfigSchema
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.utils import name_matches


@dataclass(frozen=True, slots=True)
class SkillToggles:
    allowed: Sequence[str]
    disabled: Sequence[str]
    # The writable layer's own ``disabled_skills``: ``disabled_skills``
    # concatenates across layers, so only these names can be turned back on.
    own: Sequence[str]

    def enabled(self, name: str) -> bool:
        if self.allowed:
            return name_matches(name, list(self.allowed))
        return not (self.disabled and name_matches(name, list(self.disabled)))

    def locked(self, name: str) -> bool:
        if self.allowed:
            return True
        if not self.disabled:
            return False
        patterns = [pattern for pattern in self.disabled if pattern != name]
        if name_matches(name, patterns):
            return True
        return name_matches(name, list(self.disabled)) and name not in self.own


async def set_skill_enabled(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    name: str,
    enabled: bool,
    *,
    reason: str,
) -> None:
    def toggle(current: Any) -> list[str]:
        names = [str(entry) for entry in current or []]
        if enabled:
            return [entry for entry in names if entry != name]
        return names if name in names else [*names, name]

    failures = await orchestrator.mutate_field(
        "/disabled_skills", toggle, reason=reason, default=[]
    )
    if failures:
        raise RequestFailure(
            ProtocolErrorCode.INTERNAL_ERROR,
            f"Failed to update configuration: {failures[0]}",
        ) from failures[0]
