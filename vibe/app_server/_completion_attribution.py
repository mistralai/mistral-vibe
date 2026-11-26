"""Vibe attribution ridden by Unified Harness provider requests.

A completion the Harness runtime makes carries the same session identity as the
client events for that session, so its ``quota.request_done`` row lands in the
Vibe request marts. Cache affinity is a separate request concern: forked
sessions keep their own telemetry identity while routing with their root
session. This module keeps those two late-bound sources separate for the
runtime config.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from vibe.core.telemetry.build_metadata import build_request_metadata
from vibe.core.telemetry.send import TelemetryClient
from vibe.core.telemetry.types import LaunchContext, TelemetryCallType

# One session's request attribution, resolved per completion from its purpose
# and its iteration within the turn. ``classify`` and ``title`` name the
# smart-approve risk classifier's completion and the background session title,
# both issued by the runtime outside a turn's loop.
type CompletionAttributionSource = Callable[
    [Literal["agent", "compaction", "classify", "title"], int], dict[str, str]
]
type CompletionAffinitySource = Callable[[], str | None]

# Late-binding source of the current user message id, read per completion.
# The adapter learns the id from history snapshots, so a callable (not a
# snapshot) keeps the attribution current across turns.
type MessageIdSource = Callable[[], str | None]


def request_call_type(
    purpose: Literal["agent", "compaction", "classify", "title"], iteration: int
) -> TelemetryCallType:
    """Map the runtime's purpose/iteration onto the legacy call-type taxonomy.

    Legacy marks the first LLM call of a user turn ``main_call`` and every
    follow-up — tool-driven iterations and compaction — ``secondary_call``. The
    smart-approve classifier and the background session title answer no user
    prompt; each gets its own type so request-volume metrics can exclude them.

    Both the ``vibe.request_sent`` event and the attribution ridden by the
    provider request itself read this, so the two channels can never disagree
    about what one call was.
    """
    if purpose == "classify":
        return "smart_approve"
    if purpose == "title":
        return "title_generation"
    if purpose == "agent" and iteration == 0:
        return "main_call"
    return "secondary_call"


def build_completion_attribution(
    telemetry: TelemetryClient,
    launch_context: LaunchContext | None,
    *,
    message_id_getter: MessageIdSource | None = None,
) -> CompletionAttributionSource:
    """One session's Vibe attribution, shaped as a provider request's ``metadata``.

    Reads the telemetry client on each call rather than snapshotting it, so a
    request reports the same session the client events for that session do.
    ``message_id_getter`` is read live so the attribution picks up the current
    turn's user message id, matching the legacy loop's
    ``self._current_user_message_id``.
    """

    def attribution(
        purpose: Literal["agent", "compaction", "classify", "title"], iteration: int
    ) -> dict[str, str]:
        metadata = build_request_metadata(
            launch_context=launch_context,
            session_id=telemetry.session_id,
            parent_session_id=telemetry.parent_session_id,
            call_type=request_call_type(purpose, iteration),
            message_id=(message_id_getter() if message_id_getter is not None else None),
            user_plan=telemetry.user_plan,
        )
        return {
            key: str(value)
            for key, value in metadata.model_dump(exclude_none=True).items()
        }

    return attribution


class CompletionAttributionHolder:
    """One derivation's link from the Harness runtime back to its session.

    The runtime reads a completion's metadata through the adapter config, which
    the derivation builds before the session -- and so before its id -- exists.

    One holder per derivation, never per context. A rewind or a history clear
    opens a second session against the same context, so a context-scoped holder
    would rebind to the replacement and stamp the still-live source session's
    completions with the wrong session id. Each derivation carries its own, and
    each session's runtime keeps the config it was opened with.

    Unbound it attributes nothing, which the request marts skip rather than
    misread.
    """

    __slots__ = ("_source",)

    def __init__(self) -> None:
        self._source: CompletionAttributionSource | None = None

    def bind(self, source: CompletionAttributionSource) -> None:
        self._source = source

    def metadata(
        self,
        purpose: Literal["agent", "compaction", "classify", "title"],
        iteration: int,
    ) -> dict[str, str]:
        if self._source is None:
            return {}
        return self._source(purpose, iteration)


class CompletionAffinityHolder:
    """One derivation's late-bound cache-routing identity."""

    __slots__ = ("_source",)

    def __init__(self) -> None:
        self._source: CompletionAffinitySource | None = None

    def bind(self, source: CompletionAffinitySource) -> None:
        self._source = source

    def affinity_id(self) -> str | None:
        if self._source is None:
            return None
        return self._source()
