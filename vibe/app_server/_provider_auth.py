"""Builds the redacted provider-auth snapshot behind ``providerAuth/read``.

A read-only view for ``/status``: it inspects credential *sources* — never
sends a provider request, never resolves Google ADC, and never lets credential
material or a raw storage error into the view. The precedence itself lives in
``active_api_key``, which resolution also consumes; this module reports on it
without changing it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Collection, Mapping
import concurrent.futures
from dataclasses import dataclass
import os
from pathlib import Path
import re
import threading
import unicodedata
from urllib.parse import urlsplit

from dotenv import dotenv_values
from keyring.errors import KeyringError

from vibe.app_server.provider_auth import (
    ProviderAuthKind,
    ProviderAuthView,
    ProviderCredentialSourceKind,
    ProviderCredentialSourceState,
    ProviderCredentialSourceView,
)
from vibe.core.config import EnvValueOrigin, VibeConfigSchema, env_value_origin
from vibe.core.config.models import ModelConfig, ProviderConfig
from vibe.core.paths import GLOBAL_ENV_FILE
from vibe.observability.logging import logger
from vibe.utils.api_keys import ApiKeySource, active_api_key
from vibe.utils.keyring import probe_api_key_from_keyring
from vibe.utils.url import display_url

__all__ = [
    "SourceProbe",
    "build_provider_auth_view",
    "read_provider_auth",
    "sanitize_api_base",
]

# A hanging source (blocking FIFO, wedged keychain) must not hold the command
# open: each probe gets its own deadline, and the two run concurrently.
_SOURCE_DEADLINE_SECONDS = 1.0

# Daemon threads, not ``asyncio.to_thread``'s shared executor: a timed-out
# probe is abandoned, and only a daemon can be abandoned without hanging
# interpreter shutdown. The permits bound the leak: once all are held by
# wedged probes, further probes report unavailable without waiting.
_PROBE_PERMITS = threading.BoundedSemaphore(4)
_PROBE_THREAD_NAME = "vibe-credential-probe"

_REDACTED = "[redacted]"


def _has_control_characters(value: str) -> bool:
    # Control characters (C0, DEL, and the C1 range) can move a cursor or
    # clear a screen; they have no business in a URL the user reads.
    return any(unicodedata.category(char) == "Cc" for char in value)


def _secret_pattern(secret: str) -> str:
    """A pattern matching ``secret`` raw or in any percent-encoded spelling.

    A pasted base can encode the credential with any hex case and can
    percent-encode characters ``quote`` leaves bare (RFC 3986 unreserved), so
    each character matches its raw form or its UTF-8 percent encoding. A space
    also matches ``+``, the form-style spelling a pasted value can carry.
    """
    parts = []
    for char in secret:
        encoded = "".join(f"%{byte:02x}" for byte in char.encode())
        alternatives = [re.escape(char), encoded]
        if char == " ":
            alternatives.append(r"\+")
        parts.append(f"(?:{'|'.join(alternatives)})")
    return "".join(parts)


def _redact_secrets(value: str, secrets: Collection[str]) -> str:
    # Longest first: a secret that contains another secret as a substring must
    # redact whole, or the shorter one would split its occurrences and leave
    # the remainder on screen.
    for secret in sorted(secrets, key=len, reverse=True):
        if not secret:
            continue
        # Case-insensitive so a lookalike credential redacts too — the safe
        # direction for a display path.
        value = re.sub(_secret_pattern(secret), _REDACTED, value, flags=re.IGNORECASE)
    return value


def sanitize_api_base(api_base: str, secrets: Collection[str]) -> str | None:
    """The displayable API base, or ``None`` when it is not displayable.

    Keeps the HTTP or HTTPS scheme, host, optional port, and path. Usernames,
    passwords, query parameters, and fragments are dropped, and any known
    credential value is redacted in raw or URL-encoded form.
    """
    try:
        parsed = urlsplit(api_base)
        destination = display_url(parsed)
    except ValueError:
        # ``display_url`` re-reads the port, so an invalid one lands here.
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    destination = _redact_secrets(destination, secrets)
    if _has_control_characters(destination):
        return None
    return destination


@dataclass(frozen=True, slots=True)
class SourceProbe:
    """One source's inspection result.

    ``value`` is the credential the source holds (``None`` when it holds none);
    ``unavailable`` means the source could not be inspected at all, so whether
    it holds a credential is unknown. The value never leaves this module: only
    presence feeds the view.
    """

    value: str | None = None
    unavailable: bool = False


# A checked-and-empty probe: the builder's default for callers that inspect no
# sources (the non-API-key kinds).
_EMPTY_PROBE = SourceProbe()


def probe_env_file(path: Path, env_var: str) -> SourceProbe:
    """Inspect a ``.env`` file for ``env_var`` without touching the process.

    A ``.env`` inside an unreadable directory reads as checked-and-empty,
    not unavailable: ``is_file``/``is_fifo`` swallow the stat error, mirroring
    ``load_dotenv_values``, which treats the same condition as "nothing
    injected". An unreadable file itself is reported unavailable.
    """
    try:
        if not path.is_file() and not path.is_fifo():
            return SourceProbe(value=None)
        value = dotenv_values(path).get(env_var)
    except OSError, ValueError:
        # ``dotenv_values`` raises UnicodeDecodeError (a ValueError) on a
        # non-UTF-8 file; either way the file could not be inspected.
        return SourceProbe(unavailable=True)
    if isinstance(value, str) and value:
        return SourceProbe(value=value)
    return SourceProbe(value=None)


def probe_keyring(env_var: str) -> SourceProbe:
    """Inspect the OS keyring for ``env_var``, distinguishing absent from unreadable.

    The cache-answer semantics (a cached value serves without touching the
    backend, so the probe reports the view resolution would act on) live on
    ``probe_api_key_from_keyring`` — the one place that owns them.
    """
    try:
        return SourceProbe(value=probe_api_key_from_keyring(env_var))
    except KeyringError:
        return SourceProbe(unavailable=True)


def _auth_kind(provider: ProviderConfig) -> ProviderAuthKind:
    if provider.uses_vertex:
        return ProviderAuthKind.GOOGLE_ADC
    if not provider.api_key_env_var:
        return ProviderAuthKind.NONE
    return ProviderAuthKind.API_KEY


def _shadowed_sources(
    probe: SourceProbe, kind: ProviderCredentialSourceKind
) -> list[ProviderCredentialSourceView]:
    """A source that is not the active one: listed when it holds a credential
    or cannot be inspected. A checked, empty source is omitted.
    """
    if probe.unavailable:
        return [
            ProviderCredentialSourceView(
                kind=kind, state=ProviderCredentialSourceState.UNAVAILABLE
            )
        ]
    if probe.value is not None:
        return [ProviderCredentialSourceView(kind=kind)]
    return []


def _api_key_sources(
    *,
    env_var: str,
    environ: Mapping[str, str],
    origin_of: Callable[[str], EnvValueOrigin | None] | None,
    env_file_probe: SourceProbe,
    keyring_probe: SourceProbe,
) -> list[ProviderCredentialSourceView]:
    """The ordered source list for an API-key provider.

    Resolution reads the process environment first and the keyring second;
    ``$VIBE_HOME/.env`` participates through the values injected into the
    process environment at startup, so a key in the environment that this
    process injected from ``.env`` reports ``.env`` as its source. A value the
    file gained after startup was never injected, so it holds no credential
    the session would send and lists without a marker.

    The active source comes first; the sources it shadows follow in current
    precedence order. An empty list is the "no source available" state.
    """
    # ``active_api_key`` is the same precedence resolution consumes, so the
    # in-use marker cannot drift from what a model call sends. Which source
    # the winning environment value came from — Vibe wrote it (``.env``
    # injection, a stored credential) or the parent shell exported it — is
    # provenance, not precedence, and is decided here.
    resolved = active_api_key(
        env_value=environ.get(env_var), keyring_value=keyring_probe.value
    )
    if resolved is None:
        active = None
    elif resolved[0] is ApiKeySource.ENVIRONMENT:
        origin = origin_of(env_var) if origin_of is not None else None
        if origin is EnvValueOrigin.VIBE_HOME_ENV_FILE:
            active = ProviderCredentialSourceKind.VIBE_HOME_ENV_FILE
        elif origin is EnvValueOrigin.OS_KEYRING:
            active = ProviderCredentialSourceKind.OS_KEYRING
        else:
            active = ProviderCredentialSourceKind.PROCESS_ENV
    else:
        active = ProviderCredentialSourceKind.OS_KEYRING

    shadowed: list[ProviderCredentialSourceView] = []
    if active is not ProviderCredentialSourceKind.VIBE_HOME_ENV_FILE:
        shadowed.extend(
            _shadowed_sources(
                env_file_probe, ProviderCredentialSourceKind.VIBE_HOME_ENV_FILE
            )
        )
    if active is not ProviderCredentialSourceKind.OS_KEYRING:
        shadowed.extend(
            _shadowed_sources(keyring_probe, ProviderCredentialSourceKind.OS_KEYRING)
        )
    if active is None:
        return shadowed
    return [
        ProviderCredentialSourceView(
            kind=active, state=ProviderCredentialSourceState.IN_USE
        ),
        *shadowed,
    ]


def build_provider_auth_view(
    model: ModelConfig,
    provider: ProviderConfig,
    *,
    environ: Mapping[str, str],
    origin_of: Callable[[str], EnvValueOrigin | None] | None = None,
    env_file_probe: SourceProbe = _EMPTY_PROBE,
    keyring_probe: SourceProbe = _EMPTY_PROBE,
) -> ProviderAuthView:
    """Project one active provider into the redacted public view.

    The probes must describe ``provider``'s own ``api_key_env_var``: the
    caller resolves the provider once and passes it here, so the source list
    and the redaction set cannot be built against a provider other than the
    one that was probed.
    """
    kind = _auth_kind(provider)
    env_var = provider.api_key_env_var

    # The environment credential is redacted for every kind: an ADC provider
    # never sends the configured key, but a base pasted with it still shows
    # credential material. The file and keyring values join the set only
    # where they were probed.
    secrets: list[str] = []
    if env_var and (value := environ.get(env_var)):
        secrets.append(value)
    sources: list[ProviderCredentialSourceView] = []
    if kind is ProviderAuthKind.API_KEY:
        if env_file_probe.value is not None:
            secrets.append(env_file_probe.value)
        if keyring_probe.value is not None:
            secrets.append(keyring_probe.value)
        sources = _api_key_sources(
            env_var=env_var,
            environ=environ,
            origin_of=origin_of,
            env_file_probe=env_file_probe,
            keyring_probe=keyring_probe,
        )

    return ProviderAuthView(
        model_display_name=model.display_name or model.alias,
        provider_name=provider.name,
        api_base=sanitize_api_base(provider.api_base, secrets),
        auth_kind=kind,
        api_key_env_var=env_var or None,
        env_file_path=(
            str(GLOBAL_ENV_FILE.path) if kind is ProviderAuthKind.API_KEY else None
        ),
        sources=sources,
    )


async def _probe_with_deadline(
    probe: Callable[[], SourceProbe], permits: threading.BoundedSemaphore | None = None
) -> SourceProbe:
    # A timed-out thread cannot be cancelled and is abandoned: a wedged
    # source leaks one blocked daemon thread per read. Accepted — holding
    # the command open instead is worse, and a daemon cannot block exit.
    if permits is None:
        permits = _PROBE_PERMITS
    if not permits.acquire(blocking=False):
        return SourceProbe(unavailable=True)
    result: concurrent.futures.Future[SourceProbe] = concurrent.futures.Future()
    threading.Thread(
        target=_run_probe,
        args=(probe, result, permits),
        name=_PROBE_THREAD_NAME,
        daemon=True,
    ).start()
    try:
        # ``wrap_future`` moves the answer to the loop and swallows the two
        # late-arrival cases itself: a loop already closed at interpreter
        # exit, and a deadline that already cancelled the future.
        return await asyncio.wait_for(
            asyncio.wrap_future(result), timeout=_SOURCE_DEADLINE_SECONDS
        )
    except TimeoutError:
        return SourceProbe(unavailable=True)
    except Exception as exc:
        # The exception type is all the log gets: a storage error's text is not
        # ours to republish, and it must never reach output or telemetry.
        logger.debug(
            "Provider credential source inspection failed (%s)", type(exc).__name__
        )
        return SourceProbe(unavailable=True)


def _run_probe(
    probe: Callable[[], SourceProbe],
    result: concurrent.futures.Future[SourceProbe],
    permits: threading.BoundedSemaphore,
) -> None:
    try:
        if not result.set_running_or_notify_cancel():
            return
        try:
            result.set_result(probe())
        except Exception as exc:
            result.set_exception(exc)
    finally:
        # A wedged probe never releases its permit — that is the bound on the
        # leak — so release happens only once the probe has answered.
        permits.release()


async def read_provider_auth(config: VibeConfigSchema) -> ProviderAuthView:
    """The ``/status`` snapshot: source inspection off the event loop, no network."""
    model = config.get_active_model()
    provider = config.get_provider_for_model(model)
    kind = _auth_kind(provider)
    if kind is not ProviderAuthKind.API_KEY:
        return build_provider_auth_view(model, provider, environ=os.environ)
    env_var = provider.api_key_env_var
    env_file_probe, keyring_probe = await asyncio.gather(
        _probe_with_deadline(lambda: probe_env_file(GLOBAL_ENV_FILE.path, env_var)),
        _probe_with_deadline(lambda: probe_keyring(env_var)),
    )
    return build_provider_auth_view(
        model,
        provider,
        environ=os.environ,
        origin_of=env_value_origin,
        env_file_probe=env_file_probe,
        keyring_probe=keyring_probe,
    )
