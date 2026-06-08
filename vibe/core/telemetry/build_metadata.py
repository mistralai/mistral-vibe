from __future__ import annotations

from typing import Any, cast

from vibe.core.telemetry.types import (
    AgentEntrypoint,
    AttachmentKind,
    EntrypointMetadata,
    TelemetryBaseMetadata,
    TelemetryCallType,
    TelemetryRequestMetadata,
)
from vibe.core.types import LLMMessage


def build_base_metadata(
    *,
    entrypoint_metadata: EntrypointMetadata | None,
    session_id: str | None,
    parent_session_id: str | None = None,
    experiments: dict[str, str] | None = None,
) -> dict[str, Any]:
    entrypoint_payload = (
        entrypoint_metadata.model_dump() if entrypoint_metadata is not None else {}
    )
    return cast(
        dict[str, Any],
        TelemetryBaseMetadata(
            session_id=session_id,
            parent_session_id=parent_session_id,
            experiments=experiments or None,
            **entrypoint_payload,
        ).model_dump(exclude_none=True),
    )


def build_request_metadata(
    *,
    entrypoint_metadata: EntrypointMetadata | None,
    session_id: str | None,
    parent_session_id: str | None = None,
    call_type: TelemetryCallType,
    message_id: str | None = None,
) -> TelemetryRequestMetadata:
    entrypoint_payload = (
        entrypoint_metadata.model_dump() if entrypoint_metadata is not None else {}
    )
    return TelemetryRequestMetadata(
        session_id=session_id,
        parent_session_id=parent_session_id,
        call_type=call_type,
        message_id=message_id,
        **entrypoint_payload,
    )


def build_attachment_counts(
    message: LLMMessage | None, *, supports_images: bool
) -> dict[AttachmentKind, int]:
    if message is None:
        return {}
    counts: dict[AttachmentKind, int] = {}
    if supports_images and message.images:
        counts[AttachmentKind.IMAGE] = len(message.images)
    return counts


def build_entrypoint_metadata(
    *,
    agent_entrypoint: AgentEntrypoint,
    agent_version: str,
    client_name: str,
    client_version: str,
) -> EntrypointMetadata:
    return EntrypointMetadata(
        agent_entrypoint=agent_entrypoint,
        agent_version=agent_version,
        client_name=client_name,
        client_version=client_version,
    )
