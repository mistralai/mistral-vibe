from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Final

from vibe import __version__
from vibe.core.experiments.cache import store_cached_eval_response
from vibe.core.experiments.manager import ExperimentManager
from vibe.core.experiments.models import ExperimentAttributes
from vibe.core.identity import IdentityResult, fetch_identity
from vibe.core.telemetry.send import get_mistral_provider_and_api_key
from vibe.core.utils import get_platform_id
from vibe.observability.logging import logger
from vibe.setup.auth.whoami import (
    NO_PLAN_DATA,
    WhoAmIResult,
    derive_user_plan,
    fetch_whoami,
)

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema
    from vibe.core.session.session_logger import SessionLogger
    from vibe.core.telemetry.types import LaunchContext

EXPERIMENT_IDENTITY_TIMEOUT_S: Final = 10.0

IdentityResolver = Callable[..., Awaitable[IdentityResult | None]]
WhoAmIResolver = Callable[..., Awaitable[WhoAmIResult | None]]


async def _fetch_plan_attributes(
    *,
    config: VibeConfigSchema,
    launch_context: LaunchContext | None,
    resolve_identity: IdentityResolver | None,
    resolve_whoami: WhoAmIResolver | None,
) -> tuple[ExperimentAttributes | None, str | None]:
    """Resolve the user-scoped attribute snapshot + user_plan from identity and
    ``/whoami`` (through their caches), independent of the session.

    Returns ``(attributes, user_plan)``:
    - ``(sentinel, NO_PLAN_DATA)`` — no Mistral provider configured (not our
      user); an expected absence, distinct from null.
    - ``(None, None)`` — a Mistral provider exists but the key is missing; we
      tried but could not look anything up, so leave the fields null (a signal
      of a problem in our system), and do not stamp a snapshot.
    - ``(attributes, user_plan)`` — identity + ``/whoami`` were fetched.

    The fetch is gated on a Mistral credential, never on the active backend: a
    user on a third-party model with a Mistral provider still gets their real
    plan/org. This is the single resolution shared by fresh init and resume.
    """
    provider_and_key = get_mistral_provider_and_api_key(config)
    if provider_and_key is None:
        if config.get_mistral_provider() is None:
            sentinel = _build_attributes(config, "", launch_context).model_copy(
                update={"planType": NO_PLAN_DATA, "planName": NO_PLAN_DATA}
            )
            return sentinel, NO_PLAN_DATA
        return None, None
    provider, api_key = provider_and_key
    _resolve_identity = resolve_identity or fetch_identity
    _resolve_whoami = resolve_whoami or fetch_whoami
    identity, whoami = await asyncio.gather(
        _resolve_identity(
            base_url=provider.api_base,
            api_key=api_key,
            timeout=EXPERIMENT_IDENTITY_TIMEOUT_S,
        ),
        _resolve_whoami(
            base_url=config.console_base_url,
            api_key=api_key,
            timeout=EXPERIMENT_IDENTITY_TIMEOUT_S,
        ),
    )
    logger.debug(
        "GrowthBook experiment attributes: identity_fetched=%s whoami_fetched=%s",
        identity is not None,
        whoami is not None,
    )
    attributes = _build_attributes(
        config, api_key, launch_context, identity=identity, whoami=whoami
    )
    return attributes, derive_user_plan(whoami)


async def initialize_experiments(
    *,
    config: VibeConfigSchema,
    manager: ExperimentManager,
    session_logger: SessionLogger,
    launch_context: LaunchContext | None,
    resolve_identity: IdentityResolver | None = None,
    resolve_whoami: WhoAmIResolver | None = None,
) -> tuple[bool, str | None]:
    if not config.enable_telemetry:
        return False, None
    attributes, user_plan = await _fetch_plan_attributes(
        config=config,
        launch_context=launch_context,
        resolve_identity=resolve_identity,
        resolve_whoami=resolve_whoami,
    )
    if attributes is None:
        # Mistral provider present but key missing — tried but failed.
        return False, user_plan
    if user_plan == NO_PLAN_DATA or not config.experiments.enable:
        # No Mistral provider (sentinel) or the A/B opt-out: keep the attribute
        # snapshot + user_plan for telemetry segmentation, but run no GrowthBook
        # eval (no bucketing, no eval cache, no prompt refresh, no persist).
        manager.set_attributes(attributes)
        return False, user_plan
    await manager.initialize(attributes)
    state = manager.export_state()
    if state is None:
        # Remote eval failed (network / 4xx-5xx / invalid payload). The manager
        # is fail-open and stayed empty, so nothing changed — don't trigger a
        # prompt refresh. We still surface user_plan for early telemetry events.
        return False, user_plan
    try:
        store_cached_eval_response(config, state)
    except Exception:
        logger.exception("Failed to cache experiment eval response")
    # Persist ONLY the sticky variant assignment. Plan/org attributes and
    # user_plan are user-scoped, not session-scoped, so they are never written
    # to meta.json — they are re-resolved from the user cache on every session.
    try:
        await session_logger.persist_experiments(state)
    except Exception:
        logger.exception("Failed to persist experiments")
    return True, user_plan


async def hydrate_experiments_from_session(
    *,
    config: VibeConfigSchema,
    manager: ExperimentManager,
    session_logger: SessionLogger,
) -> bool:
    """Restore ONLY the sticky GrowthBook variant assignment from ``meta.json``.

    Plan/org attributes and ``user_plan`` are user-scoped, not session-scoped,
    so they are deliberately NOT read from ``meta.json``. The caller rebuilds
    them from the identity + ``/whoami`` path (:func:`resolve_plan_attributes`),
    so a resumed session reports the user's current plan — identical to a fresh
    one, and resuming never changes the reported plan.

    Returns whether the variant assignment was hydrated (for the prompt
    refresh); the assignment stays frozen so variants do not re-bucket on
    resume.
    """
    if not config.enable_telemetry:
        return False
    metadata = session_logger.session_metadata
    if metadata is None or metadata.experiments is None:
        return False
    if not config.experiments.enable:
        return False
    manager.hydrate(metadata.experiments)
    return True


async def resolve_plan_attributes(
    *,
    config: VibeConfigSchema,
    manager: ExperimentManager,
    launch_context: LaunchContext | None,
    resolve_identity: IdentityResolver | None = None,
    resolve_whoami: WhoAmIResolver | None = None,
) -> str | None:
    """Rebuild the user-scoped attribute snapshot + ``user_plan`` from identity
    and ``/whoami`` (through their caches) and apply it via
    ``manager.set_attributes`` — never ``manager.initialize``.

    Run on resume so a resumed session's variant assignment stays frozen while
    its plan/org data reflects the current user. Because the source is the user
    cache (not ``meta.json``), resuming never changes the reported plan.
    """
    if not config.enable_telemetry:
        return None
    attributes, user_plan = await _fetch_plan_attributes(
        config=config,
        launch_context=launch_context,
        resolve_identity=resolve_identity,
        resolve_whoami=resolve_whoami,
    )
    if attributes is not None:
        manager.set_attributes(attributes)
    return user_plan


def _build_attributes(
    config: VibeConfigSchema,
    api_key: str,
    launch_context: LaunchContext | None,
    *,
    identity: IdentityResult | None = None,
    whoami: WhoAmIResult | None = None,
) -> ExperimentAttributes:
    from vibe.core.config import VibeConfigSchema

    entrypoint = launch_context.agent_entrypoint if launch_context else "unknown"
    client_name = launch_context.client_name if launch_context else None
    client_version = launch_context.client_version if launch_context else None
    agent_version = launch_context.agent_version if launch_context else __version__
    terminal_emulator = launch_context.terminal_emulator if launch_context else None
    default_prompt_id = VibeConfigSchema.model_fields["system_prompt_id"].default
    organization_id = (
        identity.organization.id if identity and identity.organization else None
    )
    organization_kind = whoami.organization_kind if whoami else None
    customer_id = whoami.customer_id if whoami else None
    plan_type = whoami.plan_type.value if whoami else None
    plan_name = whoami.plan_name if whoami else None
    workspace_id = identity.workspace.id if identity and identity.workspace else None
    user_id = identity.id if identity else None
    return ExperimentAttributes(
        userId=user_id,
        entrypoint=entrypoint,
        agent_version=agent_version,
        client_name=client_name,
        client_version=client_version,
        os=get_platform_id(),
        terminal_emulator=terminal_emulator,
        custom_system_prompt=config.system_prompt_id != default_prompt_id,
        organizationId=organization_id,
        organizationKind=organization_kind,
        workspaceId=workspace_id,
        customerId=customer_id,
        planType=plan_type,
        planName=plan_name,
    )
