# Twin: mistralai.vibe.harness.runtime.completion.profile.catalogue. Keep both copies identical below this line.
"""Layer 2: the provider's model catalogue, asked about one model at a time.

A fetcher asks one endpoint for one model's entry; the host owns its HTTP client
and credentials. `lookup_catalogue` bounds each fetch by `CATALOGUE_TIMEOUT_SECONDS`
and caches the answer per process, endpoint and credential, a failure included, so
later sessions with the same credential don't wait. Its warnings are handed out once
per process: only the lookup that fetched returns them. A failed fetch never fails the caller: it returns no entry and a warning, and
compile falls to the next layer. Values out of range are dropped with a warning.
The catalogue fills the window, max output, image input and reasoning levels; it
never sets the compaction threshold.
"""

import asyncio
import hashlib
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Protocol
from urllib.parse import quote

from .bounds import MINIMUM_CONTEXT_WINDOW
from .known_models import KNOWN_MODELS, KnownModelRow, find_known_model
from .types import (
    NO_REASONING,
    REASONING_LEVELS,
    TEXT_AND_IMAGE,
    TEXT_ONLY,
    CatalogueEntry,
    CompletionWarning,
    InputModality,
    ReasoningLevel,
    ReasoningSupport,
    WarningCode,
)

CATALOGUE_TIMEOUT_SECONDS = 3
ANTHROPIC_VERSION = "2023-06-01"
_NOT_FOUND = 404
_OK = 200
# Header names that carry a credential; only these enter the fingerprint, so a
# per-request header such as a correlation id does not defeat the cache.
_CREDENTIAL_HEADER_PARTS = ("auth", "key", "token", "secret", "cookie")


class _Response(Protocol):
    @property
    def status_code(self) -> int: ...

    def json(self) -> object: ...


class HttpClient(Protocol):
    """The part of an HTTPX client a fetcher uses, whichever HTTPX it is."""

    async def get(self, url: str, *, headers: dict[str, str]) -> _Response: ...


class CatalogueUnavailableError(Exception):
    """The catalogue answered with neither an entry nor a clear "no entry"."""


class CatalogueFetcher(Protocol):
    """Asks one provider endpoint for one model's catalogue entry."""

    @property
    def endpoint(self) -> str:
        """The catalogue's URL: the cache key and the subject of its warnings.

        An empty endpoint means the route has no catalogue.
        """
        ...

    @property
    def credential(self) -> str:
        """A fingerprint of the credential the fetch sends, never the credential.

        Part of the cache key, so one credential's answer or failure is never
        another's. Empty when the fetch sends none.
        """
        ...

    async def fetch(self, model: str) -> CatalogueEntry | None:
        """Return the model's entry, `None` when the catalogue has none, or raise."""
        ...


def catalogue_endpoint(server_url: str) -> str:
    """The models URL below a provider's server URL, with or without `/v1`."""
    return f"{server_url.rstrip('/').removesuffix('/v1')}/v1/models"


def credential_fingerprint(api_key: str | None, headers: Mapping[str, str]) -> str:
    """A short hash of an API key and the credential headers; empty when none."""
    parts = [api_key] if api_key else []
    parts.extend(
        f"{name.lower()}:{value}"
        for name, value in sorted(headers.items())
        if any(part in name.lower() for part in _CREDENTIAL_HEADER_PARTS)
    )
    if not parts:
        return ""
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class NoCatalogueFetcher:
    """A route without a catalogue: nothing is fetched and nothing is warned."""

    @property
    def endpoint(self) -> str:
        """Empty: there is no catalogue."""
        return ""

    @property
    def credential(self) -> str:
        """Empty: nothing is sent."""
        return ""

    async def fetch(self, model: str) -> CatalogueEntry | None:
        """Return no entry."""
        return None


@dataclass(frozen=True, slots=True)
class MistralCatalogueFetcher:
    """`GET {server}/v1/models/{model}`: the context length and vision support."""

    client: HttpClient
    server_url: str
    api_key: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)

    @property
    def endpoint(self) -> str:
        """The Mistral models URL."""
        return catalogue_endpoint(self.server_url)

    @property
    def credential(self) -> str:
        """The fingerprint of the key and credential headers this fetch sends."""
        return credential_fingerprint(self.api_key, self.headers)

    async def fetch(self, model: str) -> CatalogueEntry | None:
        """Return the model's entry, or `None` on a 404."""
        headers = dict(self.headers)
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        body = await _get_json(self.client, _model_url(self.endpoint, model), headers)
        return None if body is None else mistral_catalogue_entry(body)


@dataclass(frozen=True, slots=True)
class AnthropicCatalogueFetcher:
    """`GET {server}/v1/models/{model}`: input and output limits and capabilities.

    An endpoint that answers 404 (some cloud platforms have no models API) has no
    entry; that is not a failure.
    """

    client: HttpClient
    server_url: str
    api_key: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    known_models: Sequence[KnownModelRow] = KNOWN_MODELS

    @property
    def endpoint(self) -> str:
        """The Anthropic models URL."""
        return catalogue_endpoint(self.server_url)

    @property
    def credential(self) -> str:
        """The fingerprint of the key and credential headers this fetch sends."""
        return credential_fingerprint(self.api_key, self.headers)

    async def fetch(self, model: str) -> CatalogueEntry | None:
        """Return the model's entry, or `None` on a 404."""
        headers = {"anthropic-version": ANTHROPIC_VERSION, **self.headers}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        body = await _get_json(self.client, _model_url(self.endpoint, model), headers)
        if body is None:
            return None
        return anthropic_catalogue_entry(model, body, self.known_models)


def mistral_catalogue_entry(body: object) -> CatalogueEntry:
    """Map a Mistral model card: `max_context_length` and `capabilities.vision`.

    A card whose `capabilities.reasoning` is false names a model without reasoning,
    which rejects every `reasoning_effort`. A true one gives no levels, so they
    come from the next source.
    """
    data = _require_mapping(body)
    capabilities = _mapping(data.get("capabilities"))
    return CatalogueEntry(
        context_window=_int_or_none(data.get("max_context_length")),
        input_modalities=_modalities(_bool_or_none(capabilities.get("vision"))),
        reasoning=NO_REASONING if capabilities.get("reasoning") is False else None,
    )


def anthropic_catalogue_entry(
    model: str, body: object, known_models: Sequence[KnownModelRow] = KNOWN_MODELS
) -> CatalogueEntry:
    """Map an Anthropic model: limits, image input and the supported effort levels.

    The catalogue does not say whether thinking can be turned off, so that comes from
    the known-models row; a model the table lacks keeps thinking on.
    """
    data = _require_mapping(body)
    capabilities = _mapping(data.get("capabilities"))
    return CatalogueEntry(
        context_window=_int_or_none(data.get("max_input_tokens")),
        max_output_tokens=_int_or_none(data.get("max_tokens")),
        input_modalities=_modalities(_supported(capabilities.get("image_input"))),
        reasoning=_anthropic_reasoning(model, capabilities, known_models),
    )


@dataclass(frozen=True, slots=True)
class CatalogueLookup:
    """What one lookup found: the entry (if any) and the warnings to show."""

    endpoint: str
    model: str
    entry: CatalogueEntry | None
    warnings: tuple[CompletionWarning, ...] = ()


class CatalogueCache:
    """Catalogue answers for one process, keyed by endpoint, credential and model.

    An endpoint that failed stays failed for the process and that credential, so
    later sessions don't wait on it; another credential asks again. A catalogue
    warning is shown once per process: `lookup` returns it only when it fetched,
    and a host that shows what `peek` returns filters it through `unshown`.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str, str], CatalogueLookup] = {}
        self._failed: dict[tuple[str, str], CompletionWarning] = {}
        self._shown: set[CompletionWarning] = set()
        self._shown_lock = threading.Lock()

    def peek(
        self, endpoint: str, model: str, credential: str = ""
    ) -> CatalogueLookup | None:
        """Return the cached answer, its warnings included, without fetching.

        `None` when there is none. A model's own answer wins over the endpoint's
        failure record, which only stops new fetches.
        """
        if (cached := self._entries.get((endpoint, credential, model))) is not None:
            return cached
        if (failed := self._failed.get((endpoint, credential))) is not None:
            return CatalogueLookup(endpoint, model, None, (failed,))
        return None

    def unshown(
        self, warnings: Iterable[CompletionWarning]
    ) -> tuple[CompletionWarning, ...]:
        """Drop the catalogue warnings this process already showed; mark the rest.

        Other warnings pass through: their host's per-session sink handles them.
        """
        kept: list[CompletionWarning] = []
        with self._shown_lock:
            for warning in warnings:
                if warning.code is WarningCode.CATALOGUE_UNAVAILABLE:
                    if warning in self._shown:
                        continue
                    self._shown.add(warning)
                kept.append(warning)
        return tuple(kept)

    def clear(self) -> None:
        """Forget every answer and every shown warning."""
        self._entries.clear()
        self._failed.clear()
        with self._shown_lock:
            self._shown.clear()

    async def lookup(
        self,
        fetcher: CatalogueFetcher,
        model: str,
        *,
        timeout_s: float = CATALOGUE_TIMEOUT_SECONDS,
    ) -> CatalogueLookup:
        """Return the model's checked entry from this cache or one bounded fetch.

        Never raises for the catalogue: a timeout, an HTTP error or an unreadable
        answer gives no entry and a `catalogue_unavailable` warning. Only the lookup
        that fetched returns warnings; a cached answer comes without them.
        """
        endpoint = fetcher.endpoint
        if not endpoint:
            return CatalogueLookup(endpoint, model, None)
        credential = fetcher.credential
        cached = self.peek(endpoint, model, credential)
        if cached is not None:
            return replace(cached, warnings=())
        try:
            async with asyncio.timeout(timeout_s):
                raw = await fetcher.fetch(model)
        except Exception as error:
            # A catalogue never fails the session; the next source fills in.
            reason = _describe(error, timeout_s)
            warning = CompletionWarning(
                WarningCode.CATALOGUE_UNAVAILABLE,
                endpoint,
                f"the model catalogue at {endpoint} is unavailable ({reason}); "
                "using the next source",
            )
            self._failed[(endpoint, credential)] = warning
            return CatalogueLookup(endpoint, model, None, (warning,))
        if raw is None:
            lookup = CatalogueLookup(
                endpoint,
                model,
                None,
                (
                    CompletionWarning(
                        WarningCode.CATALOGUE_UNAVAILABLE,
                        endpoint,
                        f"the model catalogue at {endpoint} has no entry for {model}; "
                        "using the next source",
                    ),
                ),
            )
        else:
            entry, warnings = _checked_entry(endpoint, model, raw)
            lookup = CatalogueLookup(endpoint, model, entry, warnings)
        self._entries[(endpoint, credential, model)] = lookup
        return lookup


PROCESS_CATALOGUE_CACHE = CatalogueCache()


async def lookup_catalogue(
    fetcher: CatalogueFetcher,
    model: str,
    *,
    cache: CatalogueCache = PROCESS_CATALOGUE_CACHE,
    timeout_s: float = CATALOGUE_TIMEOUT_SECONDS,
) -> CatalogueLookup:
    """Look the model up in the process cache, fetching it at most once."""
    return await cache.lookup(fetcher, model, timeout_s=timeout_s)


def _checked_entry(
    endpoint: str, model: str, raw: CatalogueEntry
) -> tuple[CatalogueEntry, tuple[CompletionWarning, ...]]:
    """Drop the values no model can have; each dropped value raises a warning."""
    dropped: list[str] = []
    window = raw.context_window
    if window is not None and window < MINIMUM_CONTEXT_WINDOW:
        dropped.append(f"context window {window}")
        window = None
    max_output = raw.max_output_tokens
    if max_output is not None and (
        max_output <= 0 or (window is not None and max_output >= window)
    ):
        dropped.append(f"max output {max_output}")
        max_output = None
    warnings = tuple(
        CompletionWarning(
            WarningCode.CATALOGUE_UNAVAILABLE,
            model,
            f"the model catalogue at {endpoint} gives {model} an out-of-range "
            f"{value}; ignoring it",
        )
        for value in dropped
    )
    return (
        CatalogueEntry(
            context_window=window,
            max_output_tokens=max_output,
            input_modalities=raw.input_modalities,
            reasoning=raw.reasoning,
        ),
        warnings,
    )


def _anthropic_reasoning(
    model: str,
    capabilities: Mapping[str, object],
    known_models: Sequence[KnownModelRow],
) -> ReasoningSupport | None:
    effort = _mapping(capabilities.get("effort"))
    if _supported(effort) is not True:
        return None
    levels: tuple[ReasoningLevel, ...] = tuple(
        level for level in REASONING_LEVELS if _supported(effort.get(level)) is True
    )
    if not levels:
        return None
    known = find_known_model(model, known_models)
    can_turn_off = bool(known and known.reasoning and known.reasoning.can_turn_off)
    return ReasoningSupport(levels, can_turn_off=can_turn_off)


async def _get_json(
    client: HttpClient, url: str, headers: Mapping[str, str]
) -> object | None:
    response = await client.get(url, headers=dict(headers))
    if response.status_code == _NOT_FOUND:
        return None
    if response.status_code != _OK:
        raise CatalogueUnavailableError(f"HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as error:
        raise CatalogueUnavailableError("the answer is not JSON") from error


def _model_url(endpoint: str, model: str) -> str:
    return f"{endpoint}/{quote(model, safe='')}"


def _describe(error: Exception, timeout_s: float) -> str:
    if isinstance(error, TimeoutError):
        return f"no answer within {timeout_s:g} s"
    if isinstance(error, CatalogueUnavailableError):
        return str(error)
    return type(error).__name__


def _require_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CatalogueUnavailableError("the answer is not a JSON object")
    return _mapping(value)


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _modalities(images: bool | None) -> tuple[InputModality, ...] | None:
    if images is None:
        return None
    return TEXT_AND_IMAGE if images else TEXT_ONLY


def _supported(value: object) -> bool | None:
    return _bool_or_none(_mapping(value).get("supported"))


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _bool_or_none(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
