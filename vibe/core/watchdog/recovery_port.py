from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from vibe.core.watchdog.models import Incident, RecoveryDecision


class _Result(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    succeeded: bool
    detail: str | None = None


class QuiesceResult(_Result):
    pass


class CancelResult(_Result):
    pass


class IdleResult(_Result):
    pass


class RecoveryPort(Protocol):
    async def quiesce(self, incident: Incident) -> QuiesceResult: ...

    async def cancel_active(self, incident: Incident) -> CancelResult: ...

    async def wait_until_idle(self, incident: Incident) -> IdleResult: ...

    async def inject_context(self, content: str) -> None: ...

    async def continue_once(self, prompt: str, incident: Incident) -> None: ...

    async def request_approval(self, decision: RecoveryDecision) -> bool: ...
