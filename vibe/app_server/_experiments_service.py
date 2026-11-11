"""The GrowthBook eval, held and refreshed once per OS process.

Every session of an account and client used to send its own identity,
``/whoami`` and eval requests. Now the process holds one eval per account and
client, with a copy on disk for the next process: a session is served the one
held at once, an old one is refreshed in the background, and a session only
waits, within the eval's cap, for an account and client never evaluated on
this machine. Each session keeps the eval it opened with for its whole life:
a variant never moves under a running conversation, so a refresh only
reaches sessions opened after it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import hashlib
import json
import math
import time
from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError

from vibe.core.experiments._constants import (
    EVAL_CACHE_TTL_SECONDS,
    EVAL_REQUEST_TIMEOUT_SECONDS,
    build_eval_url,
)
from vibe.core.experiments.active import ExperimentSurface
from vibe.core.experiments.client import RemoteEvalClient
from vibe.core.experiments.manager import ExperimentManager
from vibe.core.experiments.session import (
    EXPERIMENT_IDENTITY_TIMEOUT_S,
    initialize_experiments,
    uses_custom_system_prompt,
)
from vibe.core.identity_cache import IdentityCache
from vibe.core.paths import EXPERIMENT_HELD_EVAL_CACHE_FILE
from vibe.core.telemetry.send import (
    credential_cache_key_is_current,
    mistral_credential_cache_key,
    resolve_credential_cache_key,
)
from vibe.core.utils.fetch_wait import FetchFailures
from vibe.core.utils.keyed_json_cache import load_keyed_record, store_keyed_entry
from vibe.core.utils.remote_cache import (
    Cached,
    CacheStore,
    RefreshPolicy,
    RemoteCache,
    RemoteFetchFailedError,
)
from vibe.setup.auth.whoami import WhoAmICache

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema
    from vibe.core.experiments.models import EvalResponse, ExperimentAttributes
    from vibe.core.platform_auth import PlatformCredentialFactory
    from vibe.core.telemetry.types import LaunchContext

# Identity and /whoami run side by side, then the eval.
EXPERIMENTS_FETCH_TIMEOUT_S: Final = (
    EXPERIMENT_IDENTITY_TIMEOUT_S + EVAL_REQUEST_TIMEOUT_SECONDS
)

EXPERIMENTS_POLICY: Final = RefreshPolicy(
    refresh_after_s=10 * 60,
    max_age_s=EVAL_CACHE_TTL_SECONDS,
    retry_after_s=30,
    max_retry_after_s=10 * 60,
    fetch_timeout_s=EXPERIMENTS_FETCH_TIMEOUT_S,
)

EvalClientFactory = Callable[[str | None], RemoteEvalClient]


@dataclass(frozen=True, slots=True)
class HeldEval:
    """The eval, with what telemetry segments it by."""

    response: EvalResponse
    attributes: ExperimentAttributes | None
    user_plan: str | None


class ExperimentsEvalFailedError(Exception):
    """No eval: the attributes and plan resolved still segment telemetry."""

    def __init__(
        self,
        message: str = "GrowthBook eval failed",
        *,
        attributes: ExperimentAttributes | None = None,
        user_plan: str | None = None,
    ) -> None:
        super().__init__(message)
        self.attributes = attributes
        self.user_plan = user_plan


@dataclass(frozen=True, slots=True)
class ExperimentsKey:
    """One account's eval, for one client.

    Compared on what the eval's answer depends on. ``config`` is what the
    fetch reads them from: a copy, so no session's live config is held by the
    process.
    """

    credential: str
    eval_url: str | None
    api_base: str
    console_base_url: str
    client: tuple[str | None, ...]
    custom_system_prompt: bool
    config: VibeConfigSchema = field(compare=False, repr=False)
    launch_context: LaunchContext = field(compare=False, repr=False)
    experiment_system_prompt_id: str | None = field(compare=False, default=None)
    credentials: PlatformCredentialFactory | None = field(
        compare=False, default=None, repr=False
    )

    @property
    def disk_key(self) -> str:
        """Everything the key is compared on, hashed: the credential is already
        anonymous, the rest says which client and endpoints it is for.
        """
        parts = [
            self.credential,
            self.eval_url,
            self.api_base,
            self.console_base_url,
            *(str(part) if part is not None else None for part in self.client),
            self.custom_system_prompt,
        ]
        digest = hashlib.sha256(json.dumps(parts).encode("utf-8")).hexdigest()
        return digest[:32]

    @classmethod
    async def of(
        cls,
        config: VibeConfigSchema,
        launch_context: LaunchContext,
        *,
        experiment_system_prompt_id: str | None = None,
        credentials: PlatformCredentialFactory | None = None,
    ) -> ExperimentsKey | None:
        """The key *config* evaluates under; ``None`` without a credential
        that names an account (a CLI OIDC tenant still unknown).
        """
        await resolve_credential_cache_key(config, credentials)
        credential = mistral_credential_cache_key(config, credentials)
        provider = config.get_mistral_provider()
        if credential is None or provider is None:
            return None
        return cls(
            credential=credential,
            eval_url=build_eval_url(
                config.experiments.api_host, config.experiments.client_key
            ),
            api_base=provider.api_base,
            console_base_url=config.console_base_url,
            client=(
                launch_context.agent_entrypoint,
                launch_context.agent_version,
                launch_context.client_name,
                launch_context.client_version,
                launch_context.terminal_emulator,
            ),
            custom_system_prompt=uses_custom_system_prompt(
                config, experiment_system_prompt_id
            ),
            config=config.model_copy(deep=True),
            launch_context=launch_context,
            experiment_system_prompt_id=experiment_system_prompt_id,
            credentials=credentials,
        )


class DiskHeldEvalStore:
    """The evals the process serves, on disk for the next process: an app
    server that restarts serves its sessions at once instead of asking
    GrowthBook again. Kept apart from the variants-only eval cache session
    builds and the launcher read, since it also carries what telemetry
    segments the eval by.
    """

    def load(self, key: ExperimentsKey) -> Cached[HeldEval] | None:
        record = load_keyed_record(
            EXPERIMENT_HELD_EVAL_CACHE_FILE.path,
            key.disk_key,
            ttl_seconds=EVAL_CACHE_TTL_SECONDS,
            parse=_parse_held_eval,
        )
        if record is None:
            return None
        held, stored_at = record
        return Cached(held, stored_at=stored_at)

    def save(self, key: ExperimentsKey, entry: Cached[HeldEval]) -> None:
        held = entry.value
        store_keyed_entry(
            EXPERIMENT_HELD_EVAL_CACHE_FILE.path,
            key.disk_key,
            {
                "response": held.response.model_dump(mode="json"),
                "attributes": (
                    held.attributes.model_dump(mode="json")
                    if held.attributes is not None
                    else None
                ),
                "user_plan": held.user_plan,
            },
            stored_at=int(entry.stored_at),
        )


def _parse_held_eval(payload: Any) -> HeldEval | None:
    from vibe.core.experiments.models import EvalResponse, ExperimentAttributes

    if not isinstance(payload, dict):
        return None
    try:
        attributes = payload.get("attributes")
        user_plan = payload.get("user_plan")
        return HeldEval(
            response=EvalResponse.model_validate(payload["response"]),
            attributes=(
                ExperimentAttributes.model_validate(attributes)
                if attributes is not None
                else None
            ),
            user_plan=user_plan if isinstance(user_plan, str) else None,
        )
    except (KeyError, ValidationError):
        return None


class ExperimentsService:
    def __init__(
        self,
        *,
        client_factory: EvalClientFactory | None = None,
        policy: RefreshPolicy = EXPERIMENTS_POLICY,
        store: CacheStore[ExperimentsKey, HeldEval] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client_factory = client_factory or (lambda url: RemoteEvalClient(url=url))
        self._policy = policy
        # Set once an eval failed or outlasted its cap: no later session of the
        # process holds its first turn for one.
        self.failures = FetchFailures()
        self._cache: RemoteCache[ExperimentsKey, HeldEval] = RemoteCache(
            "Experiments eval",
            policy,
            self._fetch,
            store=store if store is not None else DiskHeldEvalStore(),
            clock=clock,
        )

    async def evaluate(self, key: ExperimentsKey) -> HeldEval:
        """The eval held for *key*, or the first one fetched for it.

        Raises ``ExperimentsEvalFailedError`` when there is none: the fetch
        failed, failed recently, or outlasted its timeout.
        """
        try:
            # The fetch bounds itself, after reading the disk copy: a shorter
            # wait here would give up on a fetch about to answer.
            entry = await self._cache.first(key, timeout_s=math.inf)
        except RemoteFetchFailedError as exc:
            # Only a failure that leaves nothing held: a failed refresh behind
            # one is no reason for a later session to skip its wait.
            self.failures.mark()
            cause = exc.cause
            if isinstance(cause, ExperimentsEvalFailedError):
                raise cause from None
            raise ExperimentsEvalFailedError(
                str(cause) or type(cause).__name__
            ) from None
        if entry is None:
            self.failures.mark()
            raise ExperimentsEvalFailedError("GrowthBook eval timed out")
        return entry.value

    async def close(self) -> None:
        await self._cache.close()

    async def _fetch(self, key: ExperimentsKey) -> HeldEval:
        manager = ExperimentManager(client=self._client_factory(key.eval_url))
        try:
            _, user_plan = await initialize_experiments(
                config=key.config,
                manager=manager,
                launch_context=key.launch_context,
                harness=ExperimentSurface.UNIFIED,
                failures=FetchFailures(),
                # Fresh for each fetch, so a refresh reads the current plan;
                # /whoami's disk cache still saves the request.
                resolve_identity=IdentityCache().resolve,
                resolve_whoami=WhoAmICache().resolve,
                experiment_system_prompt_id=key.experiment_system_prompt_id,
                credentials=key.credentials,
            )
        finally:
            await manager.aclose()
        response = manager.export_state()
        attributes = manager.attributes()
        if response is None or attributes is None or attributes.userId is None:
            # Without the hash attribute the eval assigns nothing: not held,
            # so a later session retries once the backoff lets it.
            raise ExperimentsEvalFailedError(attributes=attributes, user_plan=user_plan)
        if not credential_cache_key_is_current(
            key.credential, key.config, key.credentials
        ):
            # A CLI OIDC sign-in during the fetch made it another account's:
            # neither held for this key nor written to disk.
            raise ExperimentsEvalFailedError("Signed in to another account")
        return HeldEval(response, attributes, user_plan)


__all__ = [
    "EXPERIMENTS_POLICY",
    "ExperimentsEvalFailedError",
    "ExperimentsKey",
    "ExperimentsService",
    "HeldEval",
]
