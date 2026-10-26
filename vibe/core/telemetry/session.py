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
from vibe.core.telemetry.types import AttachmentKind, TelemetryCallType

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

    def is_recorded(self, effect_id: str) -> bool:
        return effect_id in self._recorded

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
        message_id: str | None = None,
        attachment_counts: dict[AttachmentKind, int] | None = None,
    ) -> None:
        self._client.send_request_sent(
            model=model,
            nb_context_chars=nb_context_chars,
            nb_context_messages=nb_context_messages,
            nb_prompt_chars=nb_prompt_chars,
            call_type=call_type,
            message_id=message_id,
            attachment_counts=attachment_counts,
        )

    def record_tool_call_detected(
        self,
        *,
        model: str,
        backend: str,
        api_style: str,
        call_type: TelemetryCallType,
        reasons: list[str],
        nb_unusable_attempts: int,
        nb_dropped_tool_calls: int,
        status: Literal["success", "error"],
        message_id: str | None = None,
    ) -> None:
        self._client.send_tool_call_detected(
            model=model,
            backend=backend,
            api_style=api_style,
            call_type=call_type,
            reasons=reasons,
            nb_unusable_attempts=nb_unusable_attempts,
            nb_dropped_tool_calls=nb_dropped_tool_calls,
            status=status,
            message_id=message_id,
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
        message_id: str | None = None,
        decision: Literal["execute", "skip"] | None = None,
        approval_type: Literal["always", "never", "ask"] | None = None,
        approval_source: Literal["config", "smart", "user", "bypass", "never"]
        | None = None,
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
            message_id=message_id,
            decision=decision,
            approval_type=approval_type,
            approval_source=approval_source,
        )

    def record_subagent_tool_call_finished(
        self,
        *,
        operation: SubagentOperation,
        outcome: SubagentOutcome,
        profile_source: SubagentProfileSource | None,
        caller_agent_path: str,
        message_id: str | None = None,
    ) -> None:
        send_unified_subagent_tool_call_finished(
            self._client,
            operation=operation,
            outcome=outcome,
            model=self._model,
            caller_agent_path=caller_agent_path,
            profile_source=profile_source,
            message_id=message_id,
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

    def record_artifact_detected(
        self,
        *,
        provider: Literal["github", "gitlab"],
        cli_source: Literal["system", "bundled", "none"],
        command_matched: bool,
        message_id: str | None = None,
    ) -> None:
        """A ``gh pr create`` / ``glab mr create`` command was intercepted."""
        self._client.send_telemetry_event(
            "vibe.artifact_detected",
            {
                "provider": provider,
                "cli_source": cli_source,
                "command_matched": command_matched,
                "message_id": message_id,
            },
        )

    def record_artifact_created(
        self,
        *,
        provider: Literal["github", "gitlab"],
        is_draft: bool,
        host_type: Literal["github_com", "gitlab_com", "enterprise"],
        repo_id_resolved: bool,
        has_title: bool,
        message_id: str | None = None,
        source: Literal["detected", "discovered"] = "detected",
    ) -> None:
        """An artifact was recorded (not a duplicate).

        ``source`` distinguishes Phase 1 command interception (``detected``)
        from Phase 2 session-load discovery (``discovered``) — one event, per
        the conventions' reuse-first rule; ``discovered`` is not in the closed
        past-tense verb list, so a separate ``vibe.artifact_discovered`` event
        would violate the naming taxonomy.
        """
        self._client.send_telemetry_event(
            "vibe.artifact_created",
            {
                "provider": provider,
                "is_draft": is_draft,
                "host_type": host_type,
                "repo_id_resolved": repo_id_resolved,
                "has_title": has_title,
                "message_id": message_id,
                "source": source,
            },
        )

    def record_artifact_mark_viewed(
        self, *, nb_artifacts: int, nb_already_viewed: int
    ) -> None:
        """The client marked artifacts as viewed (Phase 3+, Desktop panel)."""
        self._client.send_telemetry_event(
            "vibe.artifact_mark_viewed",
            {"nb_artifacts": nb_artifacts, "nb_already_viewed": nb_already_viewed},
        )

    def record_artifact_detection_failed(
        self,
        *,
        error_type: str,
        provider: Literal["github", "gitlab", "unknown"],
        stage: Literal[
            "command_parse",
            "output_extract",
            "repo_id_resolve",
            "coordinates_resolve",
            "persist",
        ],
    ) -> None:
        """Artifact detection raised an unexpected error at ``stage``."""
        self._client.send_telemetry_event(
            "vibe.artifact_detection_failed",
            {"error_type": error_type, "provider": provider, "stage": stage},
        )

    def record_artifact_synced(
        self,
        *,
        provider: Literal["github", "gitlab"],
        trigger: Literal["session_start", "turn_end", "client_request"],
        changed: bool,
    ) -> None:
        """A provider sync pass refreshed an artifact (Phase 4).

        Emitted only for artifacts whose stored fields actually changed —
        an unchanged PR produces no telemetry noise.
        """
        self._client.send_telemetry_event(
            "vibe.artifact_synced",
            {"provider": provider, "trigger": trigger, "changed": changed},
        )

    def record_artifact_sync_failed(
        self,
        *,
        provider: Literal["github", "gitlab", "unknown"],
        trigger: Literal["session_start", "turn_end", "client_request"],
        error_type: str,
    ) -> None:
        """A provider sync fetch failed for one artifact (Phase 4)."""
        self._client.send_telemetry_event(
            "vibe.artifact_sync_failed",
            {"provider": provider, "trigger": trigger, "error_type": error_type},
        )
