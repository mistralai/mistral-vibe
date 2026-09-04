"""Vibe attribution ridden by Unified Harness provider requests.

A completion the Harness runtime makes carries the same session identity as the
client events for that session, so its ``quota.request_done`` row lands in the
Vibe request marts. This module owns the shape of that attribution, the
call-type taxonomy both telemetry channels read, and the late-binding holder a
derivation hands to its runtime config. The backend adapter only builds a
session's source and attaches it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from vibe.core.telemetry.build_metadata import build_request_metadata
from vibe.core.telemetry.send import TelemetryClient
from vibe.core.telemetry.types import LaunchContext, TelemetryCallType

# One session's request attribution, resolved per completion from its purpose
# and its iteration within the turn.
type CompletionAttributionSource = Callable[
    [Literal["agent", "compaction"], int], dict[str, str]
]


def request_call_type(
    purpose: Literal["agent", "compaction"], iteration: int
) -> TelemetryCallType:
    """Map the runtime's purpose/iteration onto the legacy call-type taxonomy.

    Legacy marks the first LLM call of a user turn ``main_call`` and every
    follow-up — tool-driven iterations and compaction — ``secondary_call``.

    Both the ``vibe.request_sent`` event and the attribution ridden by the
    provider request itself read this, so the two channels can never disagree
    about what one call was.
    """
    if purpose == "agent" and iteration == 0:
        return "main_call"
    return "secondary_call"


def build_completion_attribution(
    telemetry: TelemetryClient, launch_context: LaunchContext | None
) -> CompletionAttributionSource:
    """One session's Vibe attribution, shaped as a provider request's ``metadata``.

    Reads the telemetry client on each call rather than snapshotting it, so a
    request reports the same session the client events for that session do.
    """

    def attribution(
        purpose: Literal["agent", "compaction"], iteration: int
    ) -> dict[str, str]:
        metadata = build_request_metadata(
            launch_context=launch_context,
            session_id=telemetry.session_id,
            parent_session_id=telemetry.parent_session_id,
            call_type=request_call_type(purpose, iteration),
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
        self, purpose: Literal["agent", "compaction"], iteration: int
    ) -> dict[str, str]:
        if self._source is None:
            return {}
        return self._source(purpose, iteration)
