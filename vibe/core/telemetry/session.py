from __future__ import annotations

from typing import Literal

from vibe.core.telemetry.send import (
    SubagentOperation,
    SubagentOutcome,
    SubagentProfileSource,
    TelemetryClient,
    send_unified_subagent_tool_call_finished,
    send_unified_tool_call_finished,
)
from vibe.core.telemetry.types import TelemetryCallType

__all__ = ["SessionTelemetry"]


class SessionTelemetry:
    """Backend-neutral emission surface for one unified-harness session.

    Wraps the per-event ``TelemetryClient`` with the concerns the harness path
    shares across events Core produces: the active model and terminal-effect
    dedupe. The adapter reconstructs telemetry from snapshots, so it calls one
    surface (``record_*``) instead of the raw client plus free functions.

    Lifecycle events (``new_session``/``ready``/``session_closed``) and the
    client-forwarded events (startup, slash, copy, ...) stay on the client, which
    this service exposes via :pyattr:`client`.
    """

    __slots__ = ("_client", "_model", "_recorded")

    def __init__(self, client: TelemetryClient, *, model: str) -> None:
        self._client = client
        self._model = model
        self._recorded: set[str] = set()

    @property
    def client(self) -> TelemetryClient:
        return self._client

    def set_model(self, model: str) -> None:
        """Track the active model (it changes on an agent/model switch)."""
        self._model = model

    def mark_recorded(self, effect_id: str) -> None:
        """Pre-mark a resumed session's terminal effects so they never re-emit."""
        self._recorded.add(effect_id)

    def claim(self, effect_id: str) -> bool:
        """Return ``True`` the first time an effect id is seen, ``False`` after.

        A compaction checkpoint can emit two events, so callers claim the id once
        and then emit whatever the effect warrants.
        """
        if effect_id in self._recorded:
            return False
        self._recorded.add(effect_id)
        return True

    def record_request_sent(
        self,
        *,
        model: str,
        nb_context_chars: int,
        nb_context_messages: int,
        nb_prompt_chars: int,
        call_type: TelemetryCallType,
    ) -> None:
        self._client.send_request_sent(
            model=model,
            nb_context_chars=nb_context_chars,
            nb_context_messages=nb_context_messages,
            nb_prompt_chars=nb_prompt_chars,
            call_type=call_type,
        )

    def record_tool_call_finished(
        self,
        *,
        tool_name: str,
        status: Literal["success", "failure", "skipped"],
        agent_profile_name: str | None,
        nb_files_created: int,
        nb_files_modified: int,
        file_extension: str | None,
    ) -> None:
        send_unified_tool_call_finished(
            self._client,
            tool_name=tool_name,
            status=status,
            model=self._model,
            agent_profile_name=agent_profile_name,
            nb_files_created=nb_files_created,
            nb_files_modified=nb_files_modified,
            file_extension=file_extension,
        )

    def record_subagent_tool_call_finished(
        self,
        *,
        operation: SubagentOperation,
        outcome: SubagentOutcome,
        profile_source: SubagentProfileSource | None,
    ) -> None:
        send_unified_subagent_tool_call_finished(
            self._client,
            operation=operation,
            outcome=outcome,
            model=self._model,
            profile_source=profile_source,
        )

    def record_auto_compact_triggered(
        self,
        *,
        nb_context_tokens_before: int,
        auto_compact_threshold: int,
        status: Literal["success", "failure", "cancelled"],
    ) -> None:
        self._client.send_auto_compact_triggered(
            nb_context_tokens_before=nb_context_tokens_before,
            auto_compact_threshold=auto_compact_threshold,
            status=status,
        )

    def record_compaction_failed(
        self, *, reason: Literal["tool_call", "empty_summary"]
    ) -> None:
        self._client.send_compaction_failed(reason=reason)
