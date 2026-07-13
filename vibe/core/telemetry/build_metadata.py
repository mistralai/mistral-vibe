from __future__ import annotations

from typing import Any, cast

from vibe import __version__
from vibe.core.telemetry.types import (
    AgentEntrypoint,
    AttachmentKind,
    LaunchContext,
    TelemetryBaseMetadata,
    TelemetryCallType,
    TelemetryRequestMetadata,
    TerminalEmulator,
)
from vibe.core.types import LLMMessage
from vibe.core.utils.platform import get_platform_id, get_platform_version


def build_base_metadata(
    *,
    launch_context: LaunchContext | None,
    session_id: str | None,
    parent_session_id: str | None = None,
    experiments: dict[str, str] | None = None,
    user_plan: str | None = None,
) -> dict[str, Any]:
    launch_payload = (
        launch_context.telemetry_fields() if launch_context is not None else {}
    )
    return cast(
        dict[str, Any],
        TelemetryBaseMetadata(
            os=get_platform_id(),
            os_version=get_platform_version(),
            version=__version__,
            session_id=session_id,
            parent_session_id=parent_session_id,
            experiments=experiments or None,
            user_plan=user_plan,
            **launch_payload,
        ).model_dump(exclude_none=True),
    )


def build_request_metadata(
    *,
    launch_context: LaunchContext | None,
    session_id: str | None,
    parent_session_id: str | None = None,
    call_type: TelemetryCallType,
    message_id: str | None = None,
    user_plan: str | None = None,
) -> TelemetryRequestMetadata:
    launch_payload = (
        launch_context.telemetry_fields() if launch_context is not None else {}
    )
    return TelemetryRequestMetadata(
        os=get_platform_id(),
        os_version=get_platform_version(),
        version=__version__,
        session_id=session_id,
        parent_session_id=parent_session_id,
        call_type=call_type,
        message_id=message_id,
        user_plan=user_plan,
        **launch_payload,
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


def build_launch_context(
    *,
    agent_entrypoint: AgentEntrypoint,
    agent_version: str,
    client_name: str,
    client_version: str,
    terminal_emulator: TerminalEmulator | None = None,
) -> LaunchContext:
    return LaunchContext(
        agent_entrypoint=agent_entrypoint,
        agent_version=agent_version,
        client_name=client_name,
        client_version=client_version,
        terminal_emulator=terminal_emulator,
    )
