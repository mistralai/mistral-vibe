from __future__ import annotations

import os
import subprocess
import sys
from threading import Lock

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

_KEYRING_SERVICE = "ai.mistral.vibe"
_LEGACY_KEYRING_SERVICES = ("vibe",)
_DISABLE_KEYRING_ENV_VAR = "VIBE_TEST_DISABLE_KEYRING"
_cache_lock = Lock()
_api_key_cache: dict[str, str | None] = {}
# Substring in security's output when an item doesn't exist.
_SECURITY_NOT_FOUND = "could not be found"


def _is_keyring_disabled() -> bool:
    return os.environ.get(_DISABLE_KEYRING_ENV_VAR) == "1"


def _should_use_macos_security() -> bool:
    return sys.platform == "darwin"


def _run_security(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["/usr/bin/security", *args], text=True, capture_output=True, check=True
        )
    except FileNotFoundError as exc:
        raise KeyringError("Can't run macOS Keychain security command") from exc


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
            raise KeyringError("Can't load keyring backend") from exc
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
            raise KeyringError("Can't get password from macOS Keychain") from exc
        return result.stdout.removesuffix("\n")

    try:
        return keyring.get_password(service, username)
    except ImportError as exc:
        raise KeyringError("Can't load keyring backend") from exc


def _delete_password(service: str, username: str) -> None:
    if _should_use_macos_security():
        _delete_macos_password(service, username)
        return

    try:
        keyring.delete_password(service, username)
    except ImportError as exc:
        raise KeyringError("Can't load keyring backend") from exc


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
    username: str, *, search_legacy_services: bool
) -> str | None:
    services = (_KEYRING_SERVICE, *_LEGACY_KEYRING_SERVICES)
    if not search_legacy_services:
        services = (_KEYRING_SERVICE,)
    for service in services:
        api_key = _get_password(service, username)
        if api_key is None:
            continue
        if service != _KEYRING_SERVICE:
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
        if env_key in _api_key_cache:
            return _api_key_cache[env_key]

    try:
        api_key = _get_uncached_password(
            env_key, search_legacy_services=search_legacy_services
        )
    except KeyringError:
        return None

    # Keyed on the name alone, not on the flag: a miss cached by a caller that
    # skipped the legacy services would otherwise be served to one that wanted
    # them. Safe only because the two sets of names are disjoint -- env var keys
    # on one side, MCP OAuth aliases on the other.
    with _cache_lock:
        return _api_key_cache.setdefault(env_key, api_key)


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
    with _cache_lock:
        _api_key_cache.clear()
