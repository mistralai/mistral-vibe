"""Whether a deployment serves a model, cached per endpoint, model and credential."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from hashlib import sha256
from http import HTTPStatus
import json
import os
import time
from typing import TYPE_CHECKING, Any, Final

from vibe.core.llm.utility_credentials import (
    UtilityCredentials,
    credential_identity,
    holds_credential,
    resolve_credential_identity,
    sends_sign_in,
    still_signed_in_as,
)
from vibe.core.paths import UTILITY_MODEL_CACHE_FILE
from vibe.core.types import LLMMessage, Role
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from vibe.core.config import ModelConfig, ProviderConfig
    from vibe.core.llm.model_availability_port import ModelAvailabilitySource

__all__ = [
    "MODEL_AVAILABILITY",
    "PROBE_TIMEOUT_SECONDS",
    "CompletionProbeSource",
    "ModelAvailabilityCache",
]

# Refusals expire sooner: the key or the deployment may change.
_AVAILABLE_TTL_SECONDS: Final = 7 * 24 * 60 * 60
_UNAVAILABLE_TTL_SECONDS: Final = 60 * 60

# For the whole check, which runs at session open.
PROBE_TIMEOUT_SECONDS: Final = 2.0

# In-process back-off after a check that reached no verdict.
_UNDECIDED_RETRY_SECONDS: Final = 10 * 60

# How long a disk miss is trusted; another process may write meanwhile.
_DISK_RECHECK_SECONDS: Final = 60

_TRANSIENT_CLIENT_STATUSES: Final = frozenset({
    HTTPStatus.REQUEST_TIMEOUT,
    HTTPStatus.TOO_MANY_REQUESTS,
})

_PROBE_PROMPT: Final = "ping"
_PROBE_MAX_TOKENS: Final = 1


@dataclass(frozen=True, slots=True)
class _Entry:
    available: bool
    stored_at: int

    def fresh(self, now: int) -> bool:
        ttl = _AVAILABLE_TTL_SECONDS if self.available else _UNAVAILABLE_TTL_SECONDS
        return self.stored_at > now - ttl


class _SignInRefusedError(Exception):
    """The sign-in a probe sent was refused: nothing was learnt about the model.

    Raised rather than answered as undecided, which backs off for ten minutes:
    the refusal already told the sign-in, whose replacement the next session
    should probe with at once.
    """


class CompletionProbeSource:
    """One-token completion per model, until one succeeds or the budget runs out."""

    async def check(
        self,
        *,
        provider: ProviderConfig,
        models: Sequence[ModelConfig],
        timeout_seconds: float,
        credentials: UtilityCredentials | None = None,
    ) -> Mapping[str, bool]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        verdicts: dict[str, bool] = {}
        for model in models:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            available = await _probe(
                provider=provider,
                model=model,
                timeout_seconds=remaining,
                credentials=credentials,
            )
            if available is None:
                continue
            verdicts[model.name] = available
            if available:
                break
        return verdicts


class ModelAvailabilityCache:
    """Availability verdicts; reads are sync, asking is async."""

    def __init__(self, source: ModelAvailabilitySource | None = None) -> None:
        self._source: ModelAvailabilitySource = source or CompletionProbeSource()
        self._memory: dict[str, _Entry] = {}
        # Monotonic deadlines, never persisted.
        self._absent_until: dict[str, float] = {}
        self._retry_after: dict[str, float] = {}
        self._lock = asyncio.Lock()

    def peek(
        self,
        *,
        provider: ProviderConfig,
        model: ModelConfig,
        credentials: UtilityCredentials | None = None,
    ) -> bool | None:
        """The cached verdict for the credential ``credentials`` selects, or
        None when unknown.
        """
        identity = credential_identity(provider, credentials)
        if identity is None:
            return None
        return self._peek(_cache_key(provider=provider, model=model, identity=identity))

    def _peek(self, key: str) -> bool | None:
        now = int(time.time())
        entry = self._memory.get(key)
        if entry is None or not entry.fresh(now):
            self._memory.pop(key, None)
            entry = self._read(key, now)
            if entry is None:
                return None
            self._memory[key] = entry
        return entry.available

    def remember(
        self,
        *,
        provider: ProviderConfig,
        model: ModelConfig,
        available: bool,
        credentials: UtilityCredentials | None = None,
    ) -> None:
        identity = credential_identity(provider, credentials)
        if identity is None:
            return
        self._remember(
            _cache_key(provider=provider, model=model, identity=identity), available
        )

    def _remember(self, key: str, available: bool) -> None:
        entry = _Entry(available=available, stored_at=int(time.time()))
        self._memory[key] = entry
        self._absent_until.pop(key, None)
        self._retry_after.pop(key, None)
        _write_entry(key, entry)

    def reset(self) -> None:
        """Clear in-process state; the file on disk is untouched."""
        self._memory.clear()
        self._absent_until.clear()
        self._retry_after.clear()

    async def ensure_first_available(
        self,
        *,
        provider: ProviderConfig,
        models: Sequence[ModelConfig],
        timeout_seconds: float = PROBE_TIMEOUT_SECONDS,
        credentials: UtilityCredentials | None = None,
    ) -> None:
        """Ask about the unknown models (in preference order) ahead of the first available one.

        ``credentials`` is the connection's: verdicts are asked with, and kept
        for, the credential it selects. Without one, the provider's API key.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        identity = await _asking_identity(provider, credentials, timeout_seconds)
        if identity is None:
            return
        if not self._askable(provider=provider, models=models, identity=identity):
            return
        async with self._lock:
            askable = self._askable(provider=provider, models=models, identity=identity)
            if not askable:
                return
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            try:
                verdicts = await self._source.check(
                    provider=provider,
                    models=askable,
                    timeout_seconds=remaining,
                    credentials=credentials,
                )
            except _SignInRefusedError:
                return
            # Another account signed in meanwhile: these verdicts are not its.
            if not still_signed_in_as(provider, credentials, identity):
                return
            retry_after = time.monotonic() + _UNDECIDED_RETRY_SECONDS
            for model in askable:
                key = _cache_key(provider=provider, model=model, identity=identity)
                available = verdicts.get(model.name)
                if available is None:
                    self._retry_after[key] = retry_after
                else:
                    self._remember(key, available)

    def _askable(
        self, *, provider: ProviderConfig, models: Sequence[ModelConfig], identity: str
    ) -> list[ModelConfig]:
        now = time.monotonic()
        askable: list[ModelConfig] = []
        for model in models:
            key = _cache_key(provider=provider, model=model, identity=identity)
            known = self._peek(key)
            if known:
                break
            if known is None and self._retry_after.get(key, 0.0) <= now:
                askable.append(model)
        return askable

    def _read(self, key: str, now: int) -> _Entry | None:
        if self._absent_until.get(key, 0.0) > time.monotonic():
            return None
        entry = _read_entry(key)
        if entry is None or not entry.fresh(now):
            self._absent_until[key] = time.monotonic() + _DISK_RECHECK_SECONDS
            return None
        return entry


MODEL_AVAILABILITY = ModelAvailabilityCache()


async def _asking_identity(
    provider: ProviderConfig,
    credentials: UtilityCredentials | None,
    timeout_seconds: float,
) -> str | None:
    """What verdicts are asked and kept under, or None when nothing may be
    asked: no credential, or a sign-in whose account is not known in time.

    A sign-in's account is known once it has been resolved: until then there
    is no verdict to read, nor anywhere to keep one. Resolving it spends the
    probe's budget.
    """
    if not holds_credential(provider, credentials):
        return None
    try:
        async with asyncio.timeout(timeout_seconds):
            return await resolve_credential_identity(provider, credentials)
    except TimeoutError:
        return None


async def _probe(
    *,
    provider: ProviderConfig,
    model: ModelConfig,
    timeout_seconds: float,
    credentials: UtilityCredentials | None = None,
) -> bool | None:
    """True if served, False on a client error, None when inconclusive.

    Raises ``_SignInRefusedError`` when the connection's sign-in was refused.
    """
    from mistralai_vibe_local_harness.vibe import UtilityCompletionError
    from vibe.core.llm.completion import CompletionTransport, complete_once
    from vibe.core.telemetry.build_metadata import build_request_metadata
    from vibe.utils.http import system_trust_store_enabled

    start = time.perf_counter()
    try:
        async with asyncio.timeout(timeout_seconds):
            await complete_once(
                provider=provider,
                model=model,
                messages=[LLMMessage(role=Role.user, content=_PROBE_PROMPT)],
                temperature=0.0,
                max_tokens=_PROBE_MAX_TOKENS,
                # Labelled so it is never counted as a model turn.
                metadata=build_request_metadata(
                    launch_context=None, session_id=None, call_type="secondary_call"
                ).model_dump(exclude_none=True),
                transport=CompletionTransport(
                    timeout_s=timeout_seconds,
                    retry_budget_s=0,
                    stream_idle_timeout_s=None,
                    enable_system_trust_store=system_trust_store_enabled(),
                ),
                credentials=None
                if credentials is None
                else credentials.for_provider(provider.name),
            )
    except TimeoutError:
        logger.debug(
            "Availability probe for '%s' on '%s' timed out after %.1fs",
            model.name,
            provider.name,
            timeout_seconds,
        )
        return None
    except UtilityCompletionError as exc:
        if exc.rejection == "http_unauthorized" and sends_sign_in(
            provider, credentials
        ):
            # The sign-in was refused, not the model: it keeps its account
            # across a token refresh, so a refusal kept under it would outlive
            # the expired token that caused it. An API key's refusal stays one.
            logger.debug(
                "Availability probe for '%s' on '%s' reached no verdict "
                "(sign-in refused)",
                model.name,
                provider.name,
            )
            raise _SignInRefusedError from exc
        if not _is_refusal(exc.status):
            logger.debug(
                "Availability probe for '%s' on '%s' reached no verdict (status %s)",
                model.name,
                provider.name,
                exc.status,
            )
            return None
        logger.info(
            "Model '%s' is not available on provider '%s' (HTTP %s); "
            "features that would use it fall back to the active model.",
            model.name,
            provider.name,
            exc.status,
        )
        return False
    except Exception as exc:
        logger.debug(
            "Availability probe for '%s' on '%s' reached no verdict (%s)",
            model.name,
            provider.name,
            type(exc).__name__,
        )
        return None
    logger.debug(
        "Model '%s' is available on provider '%s' (%.0fms)",
        model.name,
        provider.name,
        (time.perf_counter() - start) * 1000,
    )
    return True


def _is_refusal(status: int | None) -> bool:
    """A 4xx other than a request timeout or a rate limit."""
    return (
        status is not None
        and HTTPStatus.BAD_REQUEST <= status < HTTPStatus.INTERNAL_SERVER_ERROR
        and status not in _TRANSIENT_CLIENT_STATUSES
    )


def _cache_key(*, provider: ProviderConfig, model: ModelConfig, identity: str) -> str:
    """Hashed; per credential (the API key, or the signed-in account), since
    access is granted per key and per tenant.
    """
    material = "|".join([
        provider.api_base.rstrip("/"),
        str(provider.backend),
        model.name,
        sha256(identity.encode()).hexdigest(),
    ])
    return sha256(material.encode()).hexdigest()[:32]


def _read_entry(key: str) -> _Entry | None:
    entry = _read_entries().get(key)
    if not isinstance(entry, dict):
        return None
    available = entry.get("available")
    stored_at = entry.get("stored_at_timestamp")
    if not isinstance(available, bool) or not isinstance(stored_at, int):
        return None
    return _Entry(available=available, stored_at=stored_at)


def _write_entry(key: str, entry: _Entry) -> None:
    entries = _read_entries()
    now = int(time.time())
    kept = {
        existing_key: value
        for existing_key, value in entries.items()
        if existing_key != key and _fresh_raw(value, now)
    }
    kept[key] = {"available": entry.available, "stored_at_timestamp": entry.stored_at}
    _write_entries(kept)


def _fresh_raw(value: object, now: int) -> bool:
    if not isinstance(value, dict):
        return False
    available = value.get("available")
    stored_at = value.get("stored_at_timestamp")
    if not isinstance(available, bool) or not isinstance(stored_at, int):
        return False
    return _Entry(available=available, stored_at=stored_at).fresh(now)


def _read_entries() -> dict[str, Any]:
    try:
        with UTILITY_MODEL_CACHE_FILE.path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_entries(entries: dict[str, Any]) -> None:
    cache_path = UTILITY_MODEL_CACHE_FILE.path
    tmp_path = cache_path.with_name(f".{cache_path.name}.{os.getpid()}.tmp")
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(entries, f, separators=(",", ":"))
        os.replace(tmp_path, cache_path)
    except (OSError, TypeError):
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        logger.debug(
            "Failed to write utility model cache file %s", cache_path, exc_info=True
        )
