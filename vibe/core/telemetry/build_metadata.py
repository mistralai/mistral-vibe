from __future__ import annotations

from typing import cast

from vibe.core.telemetry.types import (
    AgentEntrypoint,
    EntrypointMetadata,
    TelemetryBaseMetadata,
    TelemetryCallType,
    TelemetryRequestMetadata,
)


def build_base_metadata(
    *,
    entrypoint_metadata: EntrypointMetadata | None,
    session_id: str | None,
    parent_session_id: str | None = None,
) -> dict[str, str]:
    entrypoint_payload = (
        entrypoint_metadata.model_dump() if entrypoint_metadata is not None else {}
    )
    return cast(
        dict[str, str],
        TelemetryBaseMetadata(
            session_id=session_id,
            parent_session_id=parent_session_id,
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
