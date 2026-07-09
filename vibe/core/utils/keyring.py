from __future__ import annotations

import os
import shlex
import subprocess
import sys
from threading import Lock
from typing import Final

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

_KEYRING_SERVICE = "ai.mistral.vibe"
_LEGACY_KEYRING_SERVICES = ("vibe",)
_DISABLE_KEYRING_ENV_VAR = "VIBE_TEST_DISABLE_KEYRING"
_cache_lock = Lock()
_api_key_cache: dict[str, str | None] = {}
_SECURITY_NOT_FOUND = "could not be found"

# The Windows Credential Manager rejects blobs over CRED_MAX_CREDENTIAL_BLOB_SIZE
# (2560 bytes; keyring stores passwords as UTF-16). Larger secrets, such as MCP
# OAuth token payloads, are split across `<username>__chunk_<i>` entries with the
# main entry holding a marker recording the chunk count.
_WIN_CRED_MAX_UTF16_BYTES: Final = 2400
_WIN_CHUNK_CHARS: Final = 600
_CHUNK_MARKER_PREFIX: Final = "__vibe-chunked-v1__:"
_MAX_CHUNK_COUNT: Final = 100


class _PasswordNotFoundError(KeyringError):
    pass


def _is_keyring_disabled() -> bool:
    return os.environ.get(_DISABLE_KEYRING_ENV_VAR) == "1"


def _should_use_macos_security() -> bool:
    return sys.platform == "darwin"


def _run_security(
    args: list[str], *, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["/usr/bin/security", *args],
            input=input_text,
            text=True,
            capture_output=True,
            check=True,
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


def _uses_windows_credential_chunking() -> bool:
    return sys.platform == "win32"


def _chunk_username(username: str, index: int) -> str:
    return f"{username}__chunk_{index}"


def _exceeds_windows_blob_limit(password: str) -> bool:
    return len(password.encode("utf-16-le")) > _WIN_CRED_MAX_UTF16_BYTES


def _parse_chunk_count(value: str) -> int | None:
    if not value.startswith(_CHUNK_MARKER_PREFIX):
        return None
    suffix = value.removeprefix(_CHUNK_MARKER_PREFIX)
    if not suffix.isdigit():
        return None
    count = int(suffix)
    return count if 0 < count <= _MAX_CHUNK_COUNT else None


def _delete_chunks(service: str, username: str, *, start: int) -> None:
    for index in range(start, _MAX_CHUNK_COUNT):
        try:
            keyring.delete_password(service, _chunk_username(username, index))
        except (PasswordDeleteError, KeyringError):
            return


def _set_chunked_password(service: str, username: str, password: str) -> None:
    chunks = [
        password[index : index + _WIN_CHUNK_CHARS]
        for index in range(0, len(password), _WIN_CHUNK_CHARS)
    ]
    if len(chunks) > _MAX_CHUNK_COUNT:
        raise KeyringError("Secret is too large for the Windows Credential Manager")
    # The marker is deleted first and rewritten last so an interrupted write
    # reads back as an absent secret, never as a mix of old and new chunks.
    try:
        keyring.delete_password(service, username)
    except PasswordDeleteError:
        pass
    for index, chunk in enumerate(chunks):
        keyring.set_password(service, _chunk_username(username, index), chunk)
    keyring.set_password(service, username, f"{_CHUNK_MARKER_PREFIX}{len(chunks)}")
    _delete_chunks(service, username, start=len(chunks))


def _read_chunked_password(service: str, username: str, count: int) -> str | None:
    parts: list[str] = []
    for index in range(count):
        part = keyring.get_password(service, _chunk_username(username, index))
        if part is None:
            return None
        parts.append(part)
    return "".join(parts)


def _delete_password_with_chunks(service: str, username: str) -> None:
    value = keyring.get_password(service, username)
    keyring.delete_password(service, username)
    if value is not None and _parse_chunk_count(value) is not None:
        _delete_chunks(service, username, start=0)


def _set_password(service: str, username: str, password: str) -> None:
    if not _should_use_macos_security():
        try:
            if _uses_windows_credential_chunking() and _exceeds_windows_blob_limit(
                password
            ):
                _set_chunked_password(service, username, password)
            else:
                keyring.set_password(service, username, password)
        except ImportError as exc:
            raise KeyringError("Can't load keyring backend") from exc
        return

    try:
        _delete_macos_password(service, username)
    except PasswordDeleteError:
        pass
    command = shlex.join([
        "add-generic-password",
        "-s",
        service,
        "-a",
        username,
        "-w",
        password,
        "-A",
    ])
    try:
        _run_security(["-i"], input_text=f"{command}\n")
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
                raise _PasswordNotFoundError() from exc
            raise KeyringError("Can't get password from macOS Keychain") from exc
        return result.stdout.removesuffix("\n")

    try:
        value = keyring.get_password(service, username)
    except ImportError as exc:
        raise KeyringError("Can't load keyring backend") from exc
    if value is None or (count := _parse_chunk_count(value)) is None:
        return value
    return _read_chunked_password(service, username, count)


def _delete_password(service: str, username: str) -> None:
    if _should_use_macos_security():
        _delete_macos_password(service, username)
        return

    try:
        if _uses_windows_credential_chunking():
            _delete_password_with_chunks(service, username)
        else:
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


def _get_uncached_password(username: str) -> str | None:
    for service in (_KEYRING_SERVICE, *_LEGACY_KEYRING_SERVICES):
        try:
            api_key = _get_password(service, username)
        except _PasswordNotFoundError:
            continue
        if api_key is None:
            continue
        if service != _KEYRING_SERVICE:
            _migrate_legacy_password(username, api_key, service)
        return api_key
    return None


def get_api_key_from_keyring(env_key: str) -> str | None:
    if not env_key:
        return None
    if _is_keyring_disabled():
        return None

    with _cache_lock:
        if env_key in _api_key_cache:
            return _api_key_cache[env_key]

    try:
        api_key = _get_uncached_password(env_key)
    except KeyringError:
        return None

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
