"""Shared helpers for tests of the adapter's Session API path.

Two ways to drive the adapter:

- ``api_adapter``: a real adapter on a real Host and local runtime, built the
  way the app server builds it, so it reads Session API events and sends its
  commands through the Session API. Use it for ordering, reads, compaction,
  subagents and anything that needs a running Turn.
- ``StreamBatches`` plus ``publish``: hand-made stream batches (API events and
  ``vibe.*`` items) fed to the adapter's Session API path, one batch at a
  time, with a stub stream follower. Use it for a single translation step,
  such as one callback or one telemetry update, where a real Turn would only
  add noise.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field, replace
import itertools
from pathlib import Path
from typing import Any, cast

from pydantic import JsonValue
import pytest

from mistralai_vibe_local_harness.protocol import (
    RustCompletionResult,
    RustCompletionResultToolCallPart,
    RustCompletionSucceededEvent,
    RustTextContentBlock,
    RustTokenUsage,
)
from mistralai_vibe_local_harness.session_api.app_server.session.models import (
    Event,
    SessionEvent,
)
from mistralai_vibe_local_harness.vibe import _local_actions, create_harness_host
from mistralai_vibe_local_harness.vibe._session_api_extensions import (
    StreamBatch,
    VibeExtensionEvent,
)
from mistralai_vibe_local_harness.vibe._session_api_transport import (
    LocalSessionApiTransport,
)
from vibe.app_server._admin_config import OrgConfigSource
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._runtime import HarnessProcess
from vibe.app_server._session_api_streams import SessionBatch
from vibe.app_server._session_backend_port import (
    SessionBackendEvent,
    SessionEventSubscription,
)
from vibe.app_server._unified_harness_backend_adapter import (
    UnifiedHarnessBackendAdapter,
    UnifiedHarnessBackendHostAdapter,
    UnifiedSessionContext,
    adapt_harness_host,
)
from vibe.app_server.protocol import (
    PageRequest,
    SessionOptions,
    SessionReadParams,
    SessionStartParams,
)
from vibe.core.config.harness_files import FileSource, HarnessFilesManager

USAGE = RustTokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)


@dataclass
class ApiAdapter:
    """A real adapter on the Session API path, with its Host and subscription."""

    host: UnifiedHarnessBackendHostAdapter
    adapter: UnifiedHarnessBackendAdapter
    subscription: SessionEventSubscription
    events: AsyncIterator[SessionBackendEvent]


async def api_host(
    tmp_path: Path,
    options: SessionOptions | None = None,
    *,
    process_services: ProcessServices,
    sources: tuple[FileSource, ...] = (),
) -> UnifiedHarnessBackendHostAdapter:
    """A Host adapter whose sessions use the Session API path, as in production.

    ``sources`` are the configuration file sources it loads, for example
    ``("project",)`` to read agent profiles from the workspace. By default it
    loads none, so the user's own files cannot change a test.
    """
    session_options = options or SessionOptions(cwd=str(tmp_path), auto_approve=False)
    process = HarnessProcess(
        HarnessFilesManager(sources=sources), services=process_services
    )
    context = await process.build_unified_session_context(session_options)
    context = replace(context, storage_root=str(tmp_path / "sessions"))

    async def build_context(
        options: SessionOptions,
        *,
        require_api_key: bool = True,
        entrypoint: Any = "cli",
        org_config: OrgConfigSource,
    ) -> UnifiedSessionContext:
        del options, require_api_key, entrypoint, org_config
        return context

    return adapt_harness_host(create_harness_host(), build_context)


@asynccontextmanager
async def api_adapter(
    tmp_path: Path,
    *,
    process_services: ProcessServices,
    history_limit: int = 100,
    options: SessionOptions | None = None,
) -> AsyncIterator[ApiAdapter]:
    """Start a session, subscribe to its events, and shut everything down after.

    The adapter is the one the Host builds, with ``api=SessionApiCommands``. A
    model must be faked by the caller, for example by patching
    ``mistralai_vibe_local_harness.vibe._local_actions.execute_completion``.
    """
    host = await api_host(tmp_path, options, process_services=process_services)
    started = await host.start(
        SessionStartParams(
            agent_config=options or SessionOptions(cwd=str(tmp_path)),
            history_limit=history_limit,
        )
    )
    adapter = cast(UnifiedHarnessBackendAdapter, started.backend)
    subscription = await adapter.subscribe(
        SessionReadParams(
            session_id=adapter.session_id, history=PageRequest(limit=history_limit)
        )
    )
    events = cast(AsyncIterator[SessionBackendEvent], subscription.events)
    try:
        yield ApiAdapter(host, adapter, subscription, events)
    finally:
        await cast(Any, subscription.events).aclose()
        await host.shutdown()


@dataclass
class StreamBatches:
    """Builds stream batches for one Session, with IDs in one sequence."""

    session_id: str
    log_id: str = "test"
    _sequence: itertools.count[int] = field(default_factory=lambda: itertools.count(1))

    def batch(
        self,
        *items: SessionEvent | tuple[str, dict[str, JsonValue]],
        extensions: Sequence[tuple[str, dict[str, JsonValue]]] = (),
        emitted_at: int = 1,
    ) -> StreamBatch:
        """One Runtime update, in the runtime's order.

        ``items`` keep their order: put a detail item, such as
        ``vibe.turn_details`` or ``vibe.callback_details``, right after the API
        event it adds to. ``extensions`` are the state items (usage, queue,
        retrying, ...), which come last.
        """
        events: list[Event | VibeExtensionEvent] = []
        for item in [*items, *extensions]:
            if isinstance(item, tuple):
                item_type, data = item
                events.append(
                    VibeExtensionEvent(
                        event_id=self._next_id(),
                        emitted_at=emitted_at,
                        session_id=self.session_id,
                        type=item_type,
                        data=data,
                    )
                )
                continue
            events.append(
                Event(
                    event_id=self._next_id(),
                    emitted_at=emitted_at,
                    session_id=self.session_id,
                    root_session_id=self.session_id,
                    payload=item,
                )
            )
        return StreamBatch(events=tuple(events))

    def _next_id(self) -> str:
        return f"{self.log_id}:{next(self._sequence)}"


class StubStreams:
    """A stream follower that only records what the adapter marks or adds."""

    def __init__(self) -> None:
        self.observed_ids: dict[str, list[str]] = {}
        self.added_children: list[str] = []

    def observed(self, session_id: str, event_ids: Sequence[str]) -> None:
        self.observed_ids.setdefault(session_id, []).extend(event_ids)

    def mark_baseline(self, session_id: str, event_id: str) -> None:
        self.observed(session_id, [event_id])

    def is_observed(self, session_id: str, event_id: str) -> bool:
        return event_id in self.observed_ids.get(session_id, [])

    async def add_child(self, session_id: str) -> None:
        self.added_children.append(session_id)


async def publish(
    adapter: UnifiedHarnessBackendAdapter,
    batch: StreamBatch,
    *,
    session_id: str | None = None,
    streams: StubStreams | None = None,
) -> list[SessionBackendEvent]:
    """The client events the adapter publishes for one batch of one Session.

    The Session must already have a state in the adapter, which ``api_adapter``
    gives the root after its subscription.
    """
    target = session_id or adapter.session_id
    api_path = adapter._api_path
    assert api_path is not None
    return [
        event
        async for event in api_path._batch_events(
            cast(Any, streams or StubStreams()), SessionBatch(target, batch)
        )
    ]


def api_transport(adapter: UnifiedHarnessBackendAdapter) -> LocalSessionApiTransport:
    """The Session API transport an adapter on the Session API path sends through."""
    api_path = adapter._api_path
    assert api_path is not None
    return api_path.commands.transport


def use_answering_model(
    monkeypatch: pytest.MonkeyPatch, *, text: str = "answer"
) -> None:
    """Make the model answer every completion with ``text``."""

    async def execute_completion(
        action: Any, _messages: Any, _tools: Any, _config: Any, **_kwargs: Any
    ) -> RustCompletionSucceededEvent:
        return RustCompletionSucceededEvent(
            action_id=action.action_id,
            result=RustCompletionResult(
                parts=[RustTextContentBlock(text=text)],
                finish_reason="stop",
                usage=USAGE,
            ),
        )

    monkeypatch.setattr(_local_actions, "execute_completion", execute_completion)


def use_model_calling_one_tool(
    monkeypatch: pytest.MonkeyPatch, *, call_id: str = "0123456789abcdef"
) -> None:
    """Make the model call one unknown tool on its first completion, then answer."""
    count = 0

    async def execute_completion(
        action: Any, _messages: Any, _tools: Any, _config: Any, **_kwargs: Any
    ) -> RustCompletionSucceededEvent:
        nonlocal count
        count += 1
        parts: list[Any] = (
            [
                RustCompletionResultToolCallPart(
                    id=call_id, name="read_file", arguments_json='{"path": "a"}'
                )
            ]
            if count == 1
            else [RustTextContentBlock(text=f"answer-{count}")]
        )
        return RustCompletionSucceededEvent(
            action_id=action.action_id,
            result=RustCompletionResult(
                parts=parts,
                finish_reason="tool_call" if count == 1 else "stop",
                usage=USAGE,
            ),
        )

    monkeypatch.setattr(_local_actions, "execute_completion", execute_completion)


@asynccontextmanager
async def forwarding(harness: ApiAdapter) -> AsyncIterator[list[SessionBackendEvent]]:
    """Consume the subscription in the background, as the app server's forwarder does.

    A read waits until the stream published its bookmark, so something must
    consume the stream while the test reads.
    """
    forwarded: list[SessionBackendEvent] = []

    async def forward() -> None:
        async for backend_event in harness.events:
            forwarded.append(backend_event)

    task = asyncio.create_task(forward())
    try:
        await until(lambda: harness.adapter._events_subscribed)
        yield forwarded
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def until(condition: Callable[[], bool]) -> None:
    """Return once ``condition`` holds, polling for up to five seconds."""
    async with asyncio.timeout(5):
        while not condition():
            await asyncio.sleep(0.01)


__all__ = [
    "ApiAdapter",
    "StreamBatches",
    "StubStreams",
    "api_adapter",
    "api_host",
    "api_transport",
    "forwarding",
    "publish",
    "until",
    "use_answering_model",
    "use_model_calling_one_tool",
]
