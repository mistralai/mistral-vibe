"""Runtime-facing child Session Host contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mistralai_vibe_local_harness.vibe._subagents._models import (
    ChildCommandTarget,
    ChildGenerationRef,
    ChildTurnOutcome,
    CloseRunningTarget,
    InterruptTarget,
    SpawnTarget,
    SubagentSessionIdentity,
)


@dataclass(frozen=True, slots=True)
class ResolvedChildSessionBinding:
    """Stable, secret-free identity of one Host-materialized child profile."""

    template_digest: str
    policy_ceiling_digest: str


@dataclass(frozen=True, slots=True)
class ChildSessionHandle:
    session_id: str


@dataclass(frozen=True, slots=True)
class ChildCommandAdmission:
    target: ChildCommandTarget
    turn_id: str


class ChildSessionHost(Protocol):
    async def create_or_restore_child(
        self,
        *,
        identity: SubagentSessionIdentity,
        binding: ResolvedChildSessionBinding,
        spawn_key: str,
        start_new_actions: bool,
        require_existing: bool,
    ) -> ChildSessionHandle: ...

    async def start_child_turn(
        self,
        child: ChildSessionHandle,
        *,
        message: str,
        target: SpawnTarget,
        operation_key: str,
    ) -> ChildCommandAdmission: ...

    async def send_child_message(
        self,
        child: ChildSessionHandle,
        *,
        message: str,
        known_generation: ChildGenerationRef,
        operation_key: str,
    ) -> ChildCommandAdmission: ...

    async def interrupt_child(
        self,
        child: ChildSessionHandle,
        *,
        target: InterruptTarget | CloseRunningTarget,
        operation_key: str,
    ) -> ChildCommandAdmission: ...

    async def wait_for_child_generation(
        self, child: ChildSessionHandle, generation: int, timeout_ms: int
    ) -> ChildTurnOutcome: ...

    async def acknowledge_child_command(
        self, child: ChildSessionHandle, *, operation_key: str
    ) -> None: ...

    async def child_command_admission(
        self, child_session_id: str, *, operation_key: str
    ) -> ChildCommandAdmission | None: ...

    async def unload_child(self, child: ChildSessionHandle) -> None: ...

    async def reconfigure_child(
        self, child: ChildSessionHandle, binding: ResolvedChildSessionBinding
    ) -> bool: ...

    async def delete_child(self, child_session_id: str) -> None: ...


__all__ = [
    "ChildCommandAdmission",
    "ChildSessionHandle",
    "ChildSessionHost",
    "ResolvedChildSessionBinding",
]
