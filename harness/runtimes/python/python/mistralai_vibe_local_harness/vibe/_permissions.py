"""The seam a Host uses to scope one gated tool call's approval.

``tool_modes`` answers a question about a *tool*: may it run at all, and must it
ask first. It cannot answer a question about a *call* — ``npm test`` and
``rm -rf /`` share one ``file_system.bash`` mode between them, so a grant
recorded against the mode is a grant against every command.

A Host that wants finer grain binds a resolver. The Runtime asks it only when
the mode already says ``ask``: catalogue absence and a configured ``deny``
outrank anything a Host would widen, and an ``allow`` has nothing left to
decide. It is asked with the name the call is published under: a builtin's name,
or a provided tool's full route (``group.tool``). With no resolver bound the mode
stands on its own, so the seam is additive.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from mistralai_vibe_local_harness.session_protocol import JsonObject


@dataclass(frozen=True, slots=True)
class PermissionOutcome:
    """What a Host's resolver says about one gated tool call."""

    decision: Literal["allow", "ask", "deny"]

    required_permissions: tuple[JsonObject, ...] = ()

    reason: str | None = None
    """Why a ``deny`` was refused, surfaced to the model in the tool failure."""

    authorized_path: Path | None = None
    """The resolved path this call was cleared for, for a file tool.

    Execution acts on it rather than resolving the argument again, and honours
    it outside the workspace roots. That is how a Host grant reaches the tool.
    """


ALWAYS_ASK = PermissionOutcome(decision="ask")
"""The outcome for an unbound resolver: keep whatever the mode already decided."""


type PermissionResolver = Callable[[str, JsonObject], Awaitable[PermissionOutcome]]
"""Resolve one call, by its published tool name and arguments."""


__all__ = ["ALWAYS_ASK", "PermissionOutcome", "PermissionResolver"]
