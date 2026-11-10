from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any, Final

from vibe import __version__
from vibe.core.experiments.active import ExperimentSurface
from vibe.core.experiments.cache import (
    clear_cached_eval_responses,
    store_cached_eval_response,
)
from vibe.core.experiments.manager import ExperimentManager
from vibe.core.experiments.models import ExperimentAttributes
from vibe.core.identity import IdentityResult, fetch_identity
from vibe.core.platform_auth import PlatformCredentialFactory
from vibe.core.platform_auth._mistral import mistral_credentials, resolve_principal
from vibe.core.utils import get_platform_id
from vibe.core.utils.fetch_wait import FetchFailures
from vibe.observability.logging import logger
from vibe.setup.auth.whoami import (
    NO_PLAN_DATA,
    WhoAmIResult,
    derive_user_plan,
    fetch_whoami,
)

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema
    from vibe.core.telemetry.types import LaunchContext

EXPERIMENT_IDENTITY_TIMEOUT_S: Final = 10.0

IdentityResolver = Callable[..., Coroutine[Any, Any, IdentityResult | None]]
WhoAmIResolver = Callable[..., Coroutine[Any, Any, WhoAmIResult | None]]


async def _fetch_plan_attributes(
    *,
    config: VibeConfigSchema,
    launch_context: LaunchContext | None,
    resolve_identity: IdentityResolver | None,
    resolve_whoami: WhoAmIResolver | None,
    harness: ExperimentSurface,
    experiment_system_prompt_id: str | None = None,
    credentials: PlatformCredentialFactory | None = None,
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
    mistral = mistral_credentials(config, credentials)
    if mistral is None:
        if config.get_mistral_provider() is None:
            sentinel = _build_attributes(
                config,
                launch_context,
                harness=harness,
                experiment_system_prompt_id=experiment_system_prompt_id,
            ).model_copy(update={"planType": NO_PLAN_DATA, "planName": NO_PLAN_DATA})
            return sentinel, NO_PLAN_DATA
        return None, None
    _resolve_identity = resolve_identity or fetch_identity
    _resolve_whoami = resolve_whoami or fetch_whoami
    # Not gather: cancelled, it returns before its requests end, which outlive
    # the session that awaited them.
    async with asyncio.TaskGroup() as lookups:
        identity_lookup = lookups.create_task(
            _resolve_identity(
                base_url=mistral.provider.api_base,
                credentials=mistral.credentials,
                timeout=EXPERIMENT_IDENTITY_TIMEOUT_S,
            )
        )
        whoami_lookup = lookups.create_task(
            _resolve_whoami(
                base_url=config.console_base_url,
                credentials=mistral.credentials,
                timeout=EXPERIMENT_IDENTITY_TIMEOUT_S,
            )
        )
    identity, whoami = identity_lookup.result(), whoami_lookup.result()
    logger.debug(
        "GrowthBook experiment attributes: identity_fetched=%s whoami_fetched=%s",
        identity is not None,
        whoami is not None,
    )
    attributes = _build_attributes(
        config,
        launch_context,
        harness=harness,
        identity=identity,
        whoami=whoami,
        experiment_system_prompt_id=experiment_system_prompt_id,
    )
    return attributes, derive_user_plan(whoami)


async def resolve_plan_attributes(
    *,
    config: VibeConfigSchema,
    launch_context: LaunchContext | None,
    resolve_identity: IdentityResolver | None,
    resolve_whoami: WhoAmIResolver | None,
    harness: ExperimentSurface,
    experiment_system_prompt_id: str | None = None,
    credentials: PlatformCredentialFactory | None = None,
) -> tuple[ExperimentAttributes | None, str | None]:
    """The attribute snapshot and plan telemetry segments by, for a session
    that sends no eval; ``(None, None)`` with telemetry off.
    """
    if not config.enable_telemetry or not config.experiments.enable:
        # The `vibe` launcher reads this cache without the config, so opting out must drop it.
        clear_cached_eval_responses()
    if not config.enable_telemetry:
        return None, None
    return await _fetch_plan_attributes(
        config=config,
        launch_context=launch_context,
        resolve_identity=resolve_identity,
        resolve_whoami=resolve_whoami,
        harness=harness,
        experiment_system_prompt_id=experiment_system_prompt_id,
        credentials=credentials,
    )


async def initialize_experiments(
    *,
    config: VibeConfigSchema,
    manager: ExperimentManager,
    launch_context: LaunchContext | None,
    harness: ExperimentSurface,
    failures: FetchFailures,
    resolve_identity: IdentityResolver | None = None,
    resolve_whoami: WhoAmIResolver | None = None,
    experiment_system_prompt_id: str | None = None,
    credentials: PlatformCredentialFactory | None = None,
) -> tuple[bool, str | None]:
    """Evaluate into *manager*; answers whether it holds an eval now, and the
    user plan.
    """
    mistral = (
        mistral_credentials(config, credentials)
        if config.enable_telemetry and config.experiments.enable
        else None
    )
    cache_key = await resolve_principal(mistral) if mistral is not None else None
    attributes, user_plan = await resolve_plan_attributes(
        config=config,
        launch_context=launch_context,
        resolve_identity=resolve_identity,
        resolve_whoami=resolve_whoami,
        harness=harness,
        experiment_system_prompt_id=experiment_system_prompt_id,
        credentials=credentials,
    )
    if not config.enable_telemetry:
        return False, None
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
        failures.mark()
        return False, user_plan
    if attributes.userId is None:
        # Without the hash attribute the response carries no assignment:
        # caching it would overwrite a good entry, and a later session gains
        # nothing by waiting for another one.
        failures.mark()
    else:
        try:
            store_cached_eval_response(
                config, state, fetched_for=cache_key, credentials=credentials
            )
        except Exception:
            logger.exception("Failed to cache experiment eval response")
    return True, user_plan


def uses_custom_system_prompt(
    config: VibeConfigSchema, experiment_system_prompt_id: str | None
) -> bool:
    from vibe.core.config import VibeConfigSchema

    default_prompt_id = VibeConfigSchema.model_fields["system_prompt_id"].default
    # A prompt applied by an experiment (e.g. from the eval cache) is not the user's.
    return config.system_prompt_id not in {
        default_prompt_id,
        experiment_system_prompt_id,
    }


def _build_attributes(
    config: VibeConfigSchema,
    launch_context: LaunchContext | None,
    *,
    harness: ExperimentSurface,
    identity: IdentityResult | None = None,
    whoami: WhoAmIResult | None = None,
    experiment_system_prompt_id: str | None = None,
) -> ExperimentAttributes:
    entrypoint = launch_context.agent_entrypoint if launch_context else "unknown"
    client_name = launch_context.client_name if launch_context else None
    client_version = launch_context.client_version if launch_context else None
    agent_version = launch_context.agent_version if launch_context else __version__
    terminal_emulator = launch_context.terminal_emulator if launch_context else None
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
        harness=harness,
        agent_version=agent_version,
        client_name=client_name,
        client_version=client_version,
        os=get_platform_id(),
        terminal_emulator=terminal_emulator,
        custom_system_prompt=uses_custom_system_prompt(
            config, experiment_system_prompt_id
        ),
        organizationId=organization_id,
        organizationKind=organization_kind,
        workspaceId=workspace_id,
        customerId=customer_id,
        planType=plan_type,
        planName=plan_name,
    )
