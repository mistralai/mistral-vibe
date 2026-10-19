from __future__ import annotations

from dataclasses import dataclass
import os
import subprocess
import sys
from threading import Lock
import time

import keyring
from keyring.errors import KeyringError, NoKeyringError, PasswordDeleteError

from vibe.observability.logging import logger

_KEYRING_SERVICE = "ai.mistral.vibe"
_LEGACY_KEYRING_SERVICES = ("vibe",)
_DISABLE_KEYRING_ENV_VAR = "VIBE_TEST_DISABLE_KEYRING"
# Long enough to type a password into the unlock dialog, short enough that an
# unattended run fails instead of waiting on a dialog nobody sees.
SECURITY_TIMEOUT_SECONDS = 60
# Shared by the back-to-back reads of one startup, retried on the user's next turn.
READ_FAILURE_TTL_SECONDS = 10


@dataclass(frozen=True, slots=True)
class _ReadFailure:
    reason: str
    failed_at: float


class _KeychainTimeoutError(KeyringError):
    pass


_cache_lock = Lock()
_api_key_cache: dict[str, str | None | _ReadFailure] = {}
_read_locks: dict[str, Lock] = {}
# A Keychain that timed out on one key would make every other key wait it out again.
_keychain_timeout: _ReadFailure | None = None
# Substring in security's output when an item doesn't exist.
_SECURITY_NOT_FOUND = "could not be found"


def _is_keyring_disabled() -> bool:
    return os.environ.get(_DISABLE_KEYRING_ENV_VAR) == "1"


def _should_use_macos_security() -> bool:
    return sys.platform == "darwin"


def _run_security(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["/usr/bin/security", *args],
            text=True,
            capture_output=True,
            check=True,
            timeout=SECURITY_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise KeyringError("Can't run macOS Keychain security command") from exc
    except subprocess.TimeoutExpired as exc:
        raise _KeychainTimeoutError(
            f"macOS Keychain did not answer within {SECURITY_TIMEOUT_SECONDS}s; "
            "it may be locked or waiting for approval"
        ) from exc


def _is_security_not_found(exc: subprocess.CalledProcessError) -> bool:
    output = f"{exc.stdout or ''}\n{exc.stderr or ''}".lower()
    return _SECURITY_NOT_FOUND in output


def _delete_macos_password(service: str, username: str) -> None:
    try:
        _run_security(["delete-generic-password", "-s", service, "-a", username])
    except subprocess.CalledProcessError as exc:
        if _is_security_not_found(exc):
            raise PasswordDeleteError() from exc
        raise KeyringError("Can't delete password in macOS Keychain") from exc


def _set_password(service: str, username: str, password: str) -> None:
    if not _should_use_macos_security():
        try:
            keyring.set_password(service, username, password)
        except ImportError as exc:
            raise NoKeyringError("Can't load keyring backend") from exc
        return

    try:
        _delete_macos_password(service, username)
    except PasswordDeleteError:
        pass
    try:
        _run_security([
            "add-generic-password",
            "-s",
            service,
            "-a",
            username,
            "-w",
            password,
            "-A",
        ])
    except subprocess.CalledProcessError as exc:
        raise KeyringError("Can't store password in macOS Keychain") from exc


def _get_password(service: str, username: str) -> str | None:
    if _should_use_macos_security():
        try:
            result = _run_security([
                "find-generic-password",
                "-s",
                service,
                "-a",
                username,
                "-w",
            ])
        except subprocess.CalledProcessError as exc:
            if _is_security_not_found(exc):
                return None
            detail = (exc.stderr or "").strip()
            message = "Can't get password from macOS Keychain"
            raise KeyringError(f"{message}: {detail}" if detail else message) from exc
        return result.stdout.removesuffix("\n")

    try:
        return keyring.get_password(service, username)
    except ImportError as exc:
        raise NoKeyringError("Can't load keyring backend") from exc


def _delete_password(service: str, username: str) -> None:
    if _should_use_macos_security():
        _delete_macos_password(service, username)
        return

    try:
        keyring.delete_password(service, username)
    except ImportError as exc:
        raise NoKeyringError("Can't load keyring backend") from exc


def _migrate_legacy_password(username: str, password: str, legacy_service: str) -> None:
    try:
        _set_password(_KEYRING_SERVICE, username, password)
    except KeyringError:
        return
    try:
        _delete_password(legacy_service, username)
    except KeyringError:
        pass


def _delete_legacy_passwords(username: str) -> None:
    for legacy_service in _LEGACY_KEYRING_SERVICES:
        try:
            _delete_password(legacy_service, username)
        except KeyringError:
            pass


def _get_uncached_password(
    username: str, *, search_legacy_services: bool, migrate_legacy: bool = True
) -> str | None:
    services = (_KEYRING_SERVICE, *_LEGACY_KEYRING_SERVICES)
    if not search_legacy_services:
        services = (_KEYRING_SERVICE,)
    for service in services:
        api_key = _get_password(service, username)
        if api_key is None:
            continue
        if migrate_legacy and service != _KEYRING_SERVICE:
            _migrate_legacy_password(username, api_key, service)
        return api_key
    return None


def get_api_key_from_keyring(
    env_key: str, *, search_legacy_services: bool = True
) -> str | None:
    """Read a secret, optionally without the pre-rename service-name fallback.

    Callers whose secrets were never written under the legacy service names pass
    ``search_legacy_services=False``: on macOS every service tried is a separate
    ``security`` subprocess, and a miss otherwise pays for all of them.
    """
    if not env_key:
        return None
    if _is_keyring_disabled():
        return None

    with _cache_lock:
        _drop_expired_failure(env_key)
        if env_key in _api_key_cache:
            return _value(_api_key_cache[env_key])
        read_lock = _read_locks.setdefault(env_key, Lock())

    # One read per key at a time: on a locked Keychain every concurrent read
    # would raise its own dialog and wait out its own timeout.
    with read_lock:
        with _cache_lock:
            _drop_expired_failure(env_key)
            if env_key in _api_key_cache:
                return _value(_api_key_cache[env_key])
            recent_timeout = _recent_keychain_timeout()
        result: str | None | _ReadFailure
        try:
            result = recent_timeout or _get_uncached_password(
                env_key, search_legacy_services=search_legacy_services
            )
        except NoKeyringError:
            return None
        except KeyringError as exc:
            logger.warning("Keyring read failed for %s: %s", env_key, exc)
            result = _ReadFailure(str(exc) or type(exc).__name__, time.monotonic())
            if isinstance(exc, _KeychainTimeoutError):
                _remember_keychain_timeout(result)

        # Keyed on the name alone, not on the flag: a miss cached by a caller that
        # skipped the legacy services would otherwise be served to one that wanted
        # them. Safe only because the two sets of names are disjoint -- env var keys
        # on one side, MCP OAuth aliases on the other.
        with _cache_lock:
            return _value(_api_key_cache.setdefault(env_key, result))


def keyring_read_failure(env_key: str) -> str | None:
    with _cache_lock:
        _drop_expired_failure(env_key)
        cached = _api_key_cache.get(env_key)
    return cached.reason if isinstance(cached, _ReadFailure) else None


def _value(cached: str | None | _ReadFailure) -> str | None:
    return None if isinstance(cached, _ReadFailure) else cached


def _is_expired(failure: _ReadFailure) -> bool:
    return time.monotonic() - failure.failed_at >= READ_FAILURE_TTL_SECONDS


def _drop_expired_failure(env_key: str) -> None:
    cached = _api_key_cache.get(env_key)
    if isinstance(cached, _ReadFailure) and _is_expired(cached):
        del _api_key_cache[env_key]


def _recent_keychain_timeout() -> _ReadFailure | None:
    if _keychain_timeout is None or _is_expired(_keychain_timeout):
        return None
    return _keychain_timeout


def _remember_keychain_timeout(failure: _ReadFailure) -> None:
    global _keychain_timeout
    with _cache_lock:
        _keychain_timeout = failure


def probe_api_key_from_keyring(env_key: str) -> str | None:
    """The keyring value for ``env_key``, raising when the keyring is unreadable.

    An unreadable keyring is an inspection failure rather than a silent "no
    credential": a caller that must distinguish "no source available" from
    "cannot be inspected" (``/status``) needs the error, while a caller that
    only needs a usable key does not.

    Read-only twice over: no legacy migration write, and no cache write.
    The probe serves from the cache when resolution already put a value
    there — the view the session authenticates with — but a miss leaves it
    untouched, so inspecting a source cannot change what a later model call
    sends: a key added after the probe is still found, and the first
    resolving read still migrates the legacy entry.

    A host with no keyring backend answers ``None`` like ``get_api_key_from_keyring``
    does: the store cannot hold a key, so the source is checked-and-empty, not
    unavailable. Only a keyring that exists but cannot be read raises.
    """
    if not env_key or _is_keyring_disabled():
        return None
    with _cache_lock:
        read_lock = _read_locks.setdefault(env_key, Lock())

    # Same guards as get: one Keychain dialog per key, no second wait after a timeout.
    with read_lock:
        with _cache_lock:
            _drop_expired_failure(env_key)
            is_cached = env_key in _api_key_cache
            cached = _api_key_cache.get(env_key)
            recent_timeout = _recent_keychain_timeout()
        if isinstance(cached, _ReadFailure):
            raise KeyringError(cached.reason)
        if is_cached:
            return cached
        if recent_timeout is not None:
            raise KeyringError(recent_timeout.reason)
        try:
            return _get_uncached_password(
                env_key, search_legacy_services=True, migrate_legacy=False
            )
        except NoKeyringError:
            return None
        except _KeychainTimeoutError as exc:
            _remember_keychain_timeout(
                _ReadFailure(str(exc) or type(exc).__name__, time.monotonic())
            )
            raise


def set_api_key_in_keyring(env_key: str, api_key: str) -> None:
    if _is_keyring_disabled():
        forget_api_key_in_keyring_cache(env_key)
        raise KeyringError("keyring disabled")

    try:
        _set_password(_KEYRING_SERVICE, env_key, api_key)
    except KeyringError:
        # Write failed: the keyring has no new value, so drop any stale cache entry.
        forget_api_key_in_keyring_cache(env_key)
        raise
    _delete_legacy_passwords(env_key)
    remember_api_key_in_keyring_cache(env_key, api_key)


def delete_api_key_from_keyring(env_key: str) -> None:
    if _is_keyring_disabled():
        forget_api_key_in_keyring_cache(env_key)
        return

    missing_error: PasswordDeleteError | None = None
    operation_error: KeyringError | None = None
    delete_succeeded = False
    try:
        for service in (_KEYRING_SERVICE, *_LEGACY_KEYRING_SERVICES):
            try:
                _delete_password(service, env_key)
                delete_succeeded = True
            except PasswordDeleteError as exc:
                missing_error = missing_error or exc
            except KeyringError as exc:
                operation_error = operation_error or exc
        if operation_error is not None:
            raise operation_error
        if not delete_succeeded and missing_error is not None:
            raise missing_error
    finally:
        forget_api_key_in_keyring_cache(env_key)


def remember_api_key_in_keyring_cache(env_key: str, api_key: str) -> None:
    if not env_key:
        return
    with _cache_lock:
        _api_key_cache[env_key] = api_key


def forget_api_key_in_keyring_cache(env_key: str) -> None:
    if not env_key:
        return
    with _cache_lock:
        _api_key_cache.pop(env_key, None)


def clear_api_key_keyring_cache() -> None:
    global _keychain_timeout
    with _cache_lock:
        _api_key_cache.clear()
        _keychain_timeout = None
