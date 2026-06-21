from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from vibe import __version__
from vibe.core.experiments.manager import ExperimentManager, hash_api_key
from vibe.core.experiments.models import ExperimentAttributes
from vibe.core.telemetry.send import get_mistral_provider_and_api_key
from vibe.core.utils import get_platform_id

if TYPE_CHECKING:
    from vibe.core.config import VibeConfig
    from vibe.core.session.session_logger import SessionLogger
    from vibe.core.telemetry.types import EntrypointMetadata, TerminalEmulator


async def initialize_experiments(
    *,
    config: VibeConfig,
    manager: ExperimentManager,
    session_logger: SessionLogger,
    entrypoint_metadata: EntrypointMetadata | None,
    terminal_emulator: TerminalEmulator | None = None,
) -> bool:
    if not config.enable_telemetry or not config.experiments.enable:
        return False
    provider_and_key = get_mistral_provider_and_api_key(config)
    if provider_and_key is None:
        return False
    _, api_key = provider_and_key
    attributes = _build_attributes(
        config, api_key, entrypoint_metadata, terminal_emulator
    )
    await manager.initialize(attributes)
    state = manager.export_state()
    if state is None:
        # Remote eval failed (network / 4xx-5xx / invalid payload). The
        # manager is fail-open and stayed empty, so nothing changed —
        # don't trigger a system prompt refresh.
        return False
    with contextlib.suppress(Exception):
        await session_logger.persist_experiments(state)
    return True


async def hydrate_experiments_from_session(
    *, config: VibeConfig, manager: ExperimentManager, session_logger: SessionLogger
) -> bool:
    if not config.enable_telemetry or not config.experiments.enable:
        return False
    metadata = session_logger.session_metadata
    if metadata is None or metadata.experiments is None:
        return False
    manager.hydrate(metadata.experiments)
    return True


def _build_attributes(
    config: VibeConfig,
    api_key: str,
    entrypoint_metadata: EntrypointMetadata | None,
    terminal_emulator: TerminalEmulator | None,
) -> ExperimentAttributes:
    from vibe.core.config import VibeConfig as _VibeConfig

    entrypoint = (
        entrypoint_metadata.agent_entrypoint if entrypoint_metadata else "unknown"
    )
    client_name = entrypoint_metadata.client_name if entrypoint_metadata else None
    client_version = entrypoint_metadata.client_version if entrypoint_metadata else None
    agent_version = (
        entrypoint_metadata.agent_version if entrypoint_metadata else __version__
    )
    default_prompt_id = _VibeConfig.model_fields["system_prompt_id"].default
    return ExperimentAttributes(
        userId=hash_api_key(api_key),
        entrypoint=entrypoint,
        agent_version=agent_version,
        client_name=client_name,
        client_version=client_version,
        os=get_platform_id(),
        terminal_emulator=terminal_emulator,
        custom_system_prompt=config.system_prompt_id != default_prompt_id,
    )
