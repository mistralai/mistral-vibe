from __future__ import annotations

import asyncio
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum, auto


class SessionExecutionConflict(RuntimeError):
    pass


SHUTDOWN_TIMEOUT_SECONDS = 5.0


async def cancel_tasks[ResultT](
    tasks: Iterable[asyncio.Task[ResultT]],
    *,
    label: str,
    timeout: float = SHUTDOWN_TIMEOUT_SECONDS,
) -> list[BaseException]:
    pending = set(tasks)
    for task in pending:
        task.cancel()
    if not pending:
        return []
    done, pending = await asyncio.wait(pending, timeout=timeout)
    errors: list[BaseException] = []
    for task in done:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except BaseException as exc:
            errors.append(exc)
    if pending:
        errors.append(
            TimeoutError(f"Timed out cancelling {len(pending)} {label} task(s)")
        )
    return errors


class SessionExecutionKind(StrEnum):
    LIFECYCLE = auto()
    TURN = auto()
    TELEPORT = auto()
    SHELL = auto()


@dataclass(frozen=True, slots=True)
class ActiveSessionExecution:
    kind: SessionExecutionKind
    id: str


class SessionExecution:
    def __init__(self) -> None:
        self._active: ActiveSessionExecution | None = None

    @property
    def active(self) -> ActiveSessionExecution | None:
        return self._active

    def begin(
        self, kind: SessionExecutionKind, operation_id: str
    ) -> ActiveSessionExecution:
        if active := self._active:
            raise SessionExecutionConflict(
                f"Session is already running {active.kind.value} {active.id}"
            )
        execution = ActiveSessionExecution(kind=kind, id=operation_id)
        self._active = execution
        return execution

    def finish(self, execution: ActiveSessionExecution) -> None:
        if self._active == execution:
            self._active = None

    @contextmanager
    def reserve(self, kind: SessionExecutionKind, operation_id: str) -> Iterator[None]:
        execution = self.begin(kind, operation_id)
        try:
            yield
        finally:
            self.finish(execution)

    def require_idle(self) -> None:
        if active := self._active:
            raise SessionExecutionConflict(
                f"Session is busy running {active.kind.value} {active.id}"
            )
