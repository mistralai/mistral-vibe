"""What each subagent of a session did, for ``session/subagents/read``.

A subagent runs as a child session of the root, with its own store beside the
root's. The root's child records say which children it spawned and where
each one's lifecycle ended; each child's own session holds its token usage.
The steps, the model calls each child's agent made, and the models they
called are recorded here as they happen: no store records them. Each child
is priced at its own model's rates.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import sys
import threading
from typing import TYPE_CHECKING

from vibe.app_server.models import AgentStatsSnapshot
from vibe.app_server.protocol import SubagentRun, SubagentRunStatus, SubagentUsage
from vibe.observability.logging import logger
from vibe.utils.pricing import session_token_cost

if TYPE_CHECKING:
    from mistralai_vibe_local_harness.vibe import (  # pyright: ignore[reportMissingImports]
        RequestSentTelemetry,
        UnifiedHarnessSessionBackendHost,
    )
    from mistralai_vibe_local_harness.vibe._subagents._models import (  # pyright: ignore[reportMissingImports]
        ChildSessionRecord,
    )
    from vibe.core.config.models import ModelConfig

# A child's turn IDs are ``<child session ID>:turn:<generation>``.
_CHILD_TURN_SEPARATOR = ":turn:"

_STATUS: dict[str, SubagentRunStatus] = {
    "reserved": SubagentRunStatus.RUNNING,
    "running": SubagentRunStatus.RUNNING,
    "idle": SubagentRunStatus.IDLE,
    "creation_failed": SubagentRunStatus.FAILED,
    "creation_cleanup_pending": SubagentRunStatus.FAILED,
    "turn_failed": SubagentRunStatus.FAILED,
    "deleting_running": SubagentRunStatus.CLOSED,
    "deleting_idle": SubagentRunStatus.CLOSED,
    "tombstone": SubagentRunStatus.CLOSED,
}


class SubagentSteps:
    """Counts each child session's agent model calls, and records the models
    they called.

    The root and its children share one request-sent sink, which the runtime
    may call off the event loop.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {}
        # Each child's called models, in first-called order.
        self._models: dict[str, dict[str, None]] = {}

    def counting(
        self, sink: Callable[[RequestSentTelemetry], None]
    ) -> Callable[[RequestSentTelemetry], None]:
        """Wrap ``sink`` to count the calls it sees before passing them on."""

        def record(payload: RequestSentTelemetry) -> None:
            self.record(payload)
            sink(payload)

        return record

    def record(self, payload: RequestSentTelemetry) -> None:
        if payload.purpose != "agent" or payload.turn_id is None:
            return
        session_id, separator, _ = payload.turn_id.rpartition(_CHILD_TURN_SEPARATOR)
        if not separator:
            return
        with self._lock:
            self._counts[session_id] = self._counts.get(session_id, 0) + 1
            self._models.setdefault(session_id, {})[payload.model] = None

    def of(self, session_id: str) -> int | None:
        with self._lock:
            return self._counts.get(session_id)

    def models_of(self, session_id: str) -> list[str]:
        """The models the child's agent called, in first-called order."""
        with self._lock:
            return list(self._models.get(session_id, {}))


@dataclass(frozen=True, slots=True)
class SubagentPricing:
    """How each subagent's usage is priced.

    ``models`` are the configured models, by alias. ``root_alias`` is the
    alias of the root's model, whose rates ``root_prices`` are: the
    fallback for a child whose model is unknown.
    """

    models: Mapping[str, ModelConfig]
    root_alias: str | None
    root_prices: AgentStatsSnapshot

    def price(self, called: Sequence[str]) -> _Price:
        """The rates for a child whose agent called ``called``.

        A child is priced at its own model's rates when it called exactly
        one model, a configured one. Otherwise, as when this process saw
        none of its calls or it called several models, it is priced at the
        root's rates.
        """
        model = self._configured(called[0]) if len(called) == 1 else None
        if model is None:
            return _Price(alias=self.root_alias, prices=self.root_prices, fallback=True)
        return _Price(
            alias=model.alias,
            prices=AgentStatsSnapshot(
                input_price_per_million=model.input_price,
                output_price_per_million=model.output_price,
                cached_input_price_per_million=model.cached_input_price,
            ),
            fallback=False,
        )

    def _configured(self, name: str) -> ModelConfig | None:
        # Aliases may share a provider model name: the root's alias first.
        root = self.models.get(self.root_alias) if self.root_alias else None
        if root is not None and root.name == name:
            return root
        return next((m for m in self.models.values() if m.name == name), None)


@dataclass(frozen=True, slots=True)
class _Price:
    alias: str | None
    prices: AgentStatsSnapshot
    fallback: bool


async def read_subagent_runs(
    host: UnifiedHarnessSessionBackendHost,
    root_session_id: str,
    steps: SubagentSteps,
    *,
    storage_root: str,
    pricing: SubagentPricing,
    deadline: float,
) -> list[SubagentRun]:
    """The subagents ``root_session_id`` spawned, in spawn order.

    The root must be live: its child records are read from its runtime. A
    child whose store is gone, as a failed spawn's is, has no usage and no
    session directory. ``pricing`` prices each child's usage at its own
    model's rates, or the root's when its model is unknown.

    The child records are in memory; only the stores are slow to read. The
    store reads run side by side, and each one that does not finish by
    ``deadline``, an event-loop time, is given up: the root's for every
    child's spawn call ID, a child's for its usage and cost. Each child is
    still listed.
    """
    records = host.child_session_records(root_session_id)
    if not records:
        return []
    spawn_call_ids = asyncio.ensure_future(
        _by(deadline, _read_spawn_call_ids(host, root_session_id))
    )
    usages = await asyncio.gather(
        *(_by(deadline, _usage(host, record)) for record in records)
    )
    call_ids = await spawn_call_ids or {}
    return [
        _run(
            record,
            root_session_id,
            usage,
            steps,
            pricing,
            storage_root=storage_root,
            spawn_call_id=call_ids.get(record.child_session_id),
        )
        for record, usage in zip(records, usages, strict=True)
    ]


def _run(
    record: ChildSessionRecord,
    root_session_id: str,
    usage: SubagentUsage | None,
    steps: SubagentSteps,
    pricing: SubagentPricing,
    *,
    storage_root: str,
    spawn_call_id: str | None,
) -> SubagentRun:
    called = steps.models_of(record.child_session_id)
    price = pricing.price(called)
    return SubagentRun(
        session_id=record.child_session_id,
        parent_session_id=root_session_id,
        root_session_id=root_session_id,
        name=record.agent_name,
        agent_type=record.agent_type,
        spawn_action_id=record.spawn_action_id,
        spawn_call_id=spawn_call_id,
        status=_status(record),
        session_dir=_session_dir(storage_root, record.child_session_id),
        model=called[0] if len(called) == 1 else None,
        usage=usage,
        cost_usd=None if usage is None else _cost(usage, price.prices),
        pricing_model=None if usage is None else price.alias,
        pricing_fallback=usage is not None and price.fallback,
        steps=steps.of(record.child_session_id),
    )


def _session_dir(storage_root: str, session_id: str) -> str | None:
    # The Harness keeps each session's store here, and deletes a closed
    # child's.
    path = Path(storage_root) / "unified" / session_id
    return str(path) if path.is_dir() else None


def _cost(usage: SubagentUsage, prices: AgentStatsSnapshot) -> float:
    return session_token_cost(
        prompt_tokens=usage.input_tokens,
        completion_tokens=usage.output_tokens,
        cached_tokens=usage.cached_input_tokens,
        input_price_per_million=prices.input_price_per_million,
        output_price_per_million=prices.output_price_per_million,
        cached_input_price_per_million=prices.cached_input_price_per_million,
    )


async def _by[T](deadline: float | None, read: Awaitable[T]) -> T | None:
    """``read``'s result, or ``None`` when it does not finish by ``deadline``."""
    try:
        async with asyncio.timeout_at(deadline):
            return await read
    except TimeoutError:
        logger.warning("Gave up a subagent store read: out of time")
        return None


async def _read_spawn_call_ids(
    host: UnifiedHarnessSessionBackendHost, root_session_id: str
) -> dict[str, str]:
    from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
        SessionReadParams,
    )

    # Every entry: the spawns may be anywhere in the root's history.
    root = await host.read(
        SessionReadParams(session_id=root_session_id, history_limit=sys.maxsize)
    )
    return _spawn_call_ids(root.snapshot.state.history.entries)


def _status(record: ChildSessionRecord) -> SubagentRunStatus:
    state = record.state
    if state.type == "idle" and state.last_outcome.type == "interrupted":
        return SubagentRunStatus.INTERRUPTED
    status = _STATUS.get(record.state.type)
    if status is None:
        # A newer harness's state: still export the subagent, and its usage.
        logger.warning(
            "Subagent %s is in a lifecycle state Vibe does not know: %s",
            record.child_session_id,
            record.state.type,
        )
        return SubagentRunStatus.UNKNOWN
    return status


async def _usage(
    host: UnifiedHarnessSessionBackendHost, record: ChildSessionRecord
) -> SubagentUsage | None:
    from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
        SessionReadParams,
    )

    try:
        read = await host.read(
            SessionReadParams(session_id=record.child_session_id, history_limit=0)
        )
    except Exception:
        logger.debug(
            "No session to read usage from for subagent %s",
            record.child_session_id,
            exc_info=True,
        )
        return None
    usage = read.snapshot.state.session.token_usage
    if usage is None:
        return None
    return SubagentUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        total_tokens=usage.total_tokens,
    )


def _spawn_call_ids(entries: Sequence[object]) -> dict[str, str]:
    """Each child's spawning tool call, from the spawn effects' annotations."""
    found: dict[str, str] = {}
    for entry in entries:
        match entry:
            case {
                "state": {
                    "output": {
                        "_meta": {
                            "mistral.vibe.subagent": {
                                "childSessionId": str() as child,
                                "spawnCallId": str() as call,
                            }
                        }
                    }
                }
            }:
                found[child] = call
            case _:
                pass
    return found


__all__ = ["SubagentPricing", "SubagentSteps", "read_subagent_runs"]
