from __future__ import annotations

import subprocess
from threading import Event, Thread

import keyring
from keyring.errors import KeyringError, PasswordDeleteError
import pytest

import vibe.core.utils.keyring as keyring_utils
from vibe.core.utils.keyring import (
    delete_api_key_from_keyring,
    get_api_key_from_keyring,
    set_api_key_in_keyring,
)

_CURRENT_SERVICE = "ai.mistral.vibe"
_RELEASED_LEGACY_SERVICE = "vibe"
_LEGACY_SERVICES = (_RELEASED_LEGACY_SERVICE,)
_ALL_SERVICES = (_CURRENT_SERVICE, *_LEGACY_SERVICES)


def test_set_writes_to_current_vibe_service_and_deletes_legacy_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[tuple[str, str, str]] = []
    deletes: list[tuple[str, str]] = []
    monkeypatch.setattr(
        keyring,
        "set_password",
        lambda service, username, password: writes.append((
            service,
            username,
            password,
        )),
    )
    monkeypatch.setattr(
        keyring,
        "delete_password",
        lambda service, username: deletes.append((service, username)),
    )

    set_api_key_in_keyring("CUSTOM_API_KEY", "new-key")

    assert writes == [(_CURRENT_SERVICE, "CUSTOM_API_KEY", "new-key")]
    assert deletes == [(service, "CUSTOM_API_KEY") for service in _LEGACY_SERVICES]


def test_set_ignores_legacy_deletion_errors_after_successful_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        keyring,
        "set_password",
        lambda service, username, password: writes.append((
            service,
            username,
            password,
        )),
    )

    def _delete_failed(service: str, username: str) -> None:
        raise KeyringError("delete failed")

    monkeypatch.setattr(keyring, "delete_password", _delete_failed)

    set_api_key_in_keyring("CUSTOM_API_KEY", "new-key")

    assert writes == [(_CURRENT_SERVICE, "CUSTOM_API_KEY", "new-key")]
    assert get_api_key_from_keyring("CUSTOM_API_KEY") == "new-key"


def test_set_populates_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        keyring, "set_password", lambda service, username, password: None
    )

    set_api_key_in_keyring("CUSTOM_API_KEY", "new-key")

    # A subsequent read is served from cache without consulting the backend.
    def _fail(service: str, username: str) -> str | None:
        raise AssertionError("keyring must not be consulted; value is cached")

    monkeypatch.setattr(keyring, "get_password", _fail)
    assert get_api_key_from_keyring("CUSTOM_API_KEY") == "new-key"


def test_get_does_not_overwrite_concurrent_cache_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_started = Event()
    allow_read_to_finish = Event()
    results: list[str | None] = []
    errors: list[BaseException] = []

    def _get(service: str, username: str) -> str | None:
        if service != _CURRENT_SERVICE:
            return None
        read_started.set()
        assert allow_read_to_finish.wait(timeout=5)
        return "old-key"

    monkeypatch.setattr(keyring, "get_password", _get)
    monkeypatch.setattr(
        keyring, "set_password", lambda service, username, password: None
    )
    monkeypatch.setattr(keyring, "delete_password", lambda service, username: None)

    def _read_key() -> None:
        try:
            results.append(get_api_key_from_keyring("CUSTOM_API_KEY"))
        except BaseException as exc:
            errors.append(exc)

    reader = Thread(target=_read_key)
    reader.start()
    assert read_started.wait(timeout=5)

    set_api_key_in_keyring("CUSTOM_API_KEY", "new-key")
    allow_read_to_finish.set()
    reader.join(timeout=5)

    assert not reader.is_alive()
    if errors:
        raise errors[0]
    assert results == ["new-key"]
    assert get_api_key_from_keyring("CUSTOM_API_KEY") == "new-key"


def test_disabled_keyring_get_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIBE_TEST_DISABLE_KEYRING", "1")

    def _fail(service: str, username: str) -> str | None:
        raise AssertionError("disabled keyring must not be consulted")

    monkeypatch.setattr(keyring, "get_password", _fail)

    assert get_api_key_from_keyring("CUSTOM_API_KEY") is None


def test_disabled_keyring_set_raises_and_forgets_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        keyring, "set_password", lambda service, username, password: None
    )
    set_api_key_in_keyring("CUSTOM_API_KEY", "cached-key")
    monkeypatch.setenv("VIBE_TEST_DISABLE_KEYRING", "1")

    with pytest.raises(KeyringError):
        set_api_key_in_keyring("CUSTOM_API_KEY", "new-key")

    monkeypatch.delenv("VIBE_TEST_DISABLE_KEYRING")
    monkeypatch.setattr(keyring, "get_password", lambda service, username: None)
    assert get_api_key_from_keyring("CUSTOM_API_KEY") is None


def test_disabled_keyring_delete_forgets_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        keyring, "set_password", lambda service, username, password: None
    )
    set_api_key_in_keyring("CUSTOM_API_KEY", "cached-key")
    monkeypatch.setenv("VIBE_TEST_DISABLE_KEYRING", "1")

    delete_api_key_from_keyring("CUSTOM_API_KEY")

    monkeypatch.delenv("VIBE_TEST_DISABLE_KEYRING")
    monkeypatch.setattr(keyring, "get_password", lambda service, username: None)
    assert get_api_key_from_keyring("CUSTOM_API_KEY") is None


def test_set_forgets_stale_cache_and_propagates_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Seed the cache with a stale value.
    monkeypatch.setattr(keyring, "get_password", lambda service, username: "stale")
    assert get_api_key_from_keyring("CUSTOM_API_KEY") == "stale"

    def _unavailable(service: str, username: str, password: str) -> None:
        raise KeyringError("no keyring")

    monkeypatch.setattr(keyring, "set_password", _unavailable)

    with pytest.raises(KeyringError):
        set_api_key_in_keyring("CUSTOM_API_KEY", "new-key")

    # The stale entry was dropped, so the next read consults the backend again.
    reads: list[tuple[str, str]] = []

    def _get(service: str, username: str) -> str | None:
        reads.append((service, username))
        return None

    monkeypatch.setattr(keyring, "get_password", _get)
    assert get_api_key_from_keyring("CUSTOM_API_KEY") is None
    assert reads == [
        (_CURRENT_SERVICE, "CUSTOM_API_KEY"),
        (_RELEASED_LEGACY_SERVICE, "CUSTOM_API_KEY"),
    ]


def test_get_prefers_current_service(monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[tuple[str, str]] = []

    def _get(service: str, username: str) -> str | None:
        reads.append((service, username))
        if service == _CURRENT_SERVICE:
            return "current-key"
        return "legacy-key"

    monkeypatch.setattr(keyring, "get_password", _get)

    assert get_api_key_from_keyring("CUSTOM_API_KEY") == "current-key"
    assert reads == [(_CURRENT_SERVICE, "CUSTOM_API_KEY")]


@pytest.mark.parametrize("legacy_service", _LEGACY_SERVICES)
def test_get_migrates_legacy_service_value(
    monkeypatch: pytest.MonkeyPatch, legacy_service: str
) -> None:
    reads: list[tuple[str, str]] = []
    writes: list[tuple[str, str, str]] = []
    deletes: list[tuple[str, str]] = []

    def _get(service: str, username: str) -> str | None:
        reads.append((service, username))
        if service == legacy_service:
            return "legacy-key"
        return None

    monkeypatch.setattr(keyring, "get_password", _get)
    monkeypatch.setattr(
        keyring,
        "set_password",
        lambda service, username, password: writes.append((
            service,
            username,
            password,
        )),
    )
    monkeypatch.setattr(
        keyring,
        "delete_password",
        lambda service, username: deletes.append((service, username)),
    )

    assert get_api_key_from_keyring("CUSTOM_API_KEY") == "legacy-key"
    expected_services = _ALL_SERVICES[: _ALL_SERVICES.index(legacy_service) + 1]
    assert reads == [(service, "CUSTOM_API_KEY") for service in expected_services]
    assert writes == [(_CURRENT_SERVICE, "CUSTOM_API_KEY", "legacy-key")]
    assert deletes == [(legacy_service, "CUSTOM_API_KEY")]


@pytest.mark.parametrize("legacy_service", _LEGACY_SERVICES)
def test_get_returns_legacy_value_when_migration_write_fails(
    monkeypatch: pytest.MonkeyPatch, legacy_service: str
) -> None:
    deletes: list[tuple[str, str]] = []

    def _get(service: str, username: str) -> str | None:
        if service == legacy_service:
            return "legacy-key"
        return None

    def _unavailable(service: str, username: str, password: str) -> None:
        raise KeyringError("no keyring")

    monkeypatch.setattr(keyring, "get_password", _get)
    monkeypatch.setattr(keyring, "set_password", _unavailable)
    monkeypatch.setattr(
        keyring,
        "delete_password",
        lambda service, username: deletes.append((service, username)),
    )

    assert get_api_key_from_keyring("CUSTOM_API_KEY") == "legacy-key"
    assert deletes == []


@pytest.mark.parametrize("legacy_service", _LEGACY_SERVICES)
def test_get_ignores_legacy_deletion_error_after_migration(
    monkeypatch: pytest.MonkeyPatch, legacy_service: str
) -> None:
    def _get(service: str, username: str) -> str | None:
        if service == legacy_service:
            return "legacy-key"
        return None

    def _delete_failed(service: str, username: str) -> None:
        raise KeyringError("delete failed")

    monkeypatch.setattr(keyring, "get_password", _get)
    monkeypatch.setattr(
        keyring, "set_password", lambda service, username, password: None
    )
    monkeypatch.setattr(keyring, "delete_password", _delete_failed)

    assert get_api_key_from_keyring("CUSTOM_API_KEY") == "legacy-key"


def test_delete_uses_current_and_legacy_vibe_services_and_forgets_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Seed the cache.
    monkeypatch.setattr(keyring, "get_password", lambda service, username: "cached")
    assert get_api_key_from_keyring("CUSTOM_API_KEY") == "cached"

    deleted: list[tuple[str, str]] = []
    monkeypatch.setattr(
        keyring,
        "delete_password",
        lambda service, username: deleted.append((service, username)),
    )

    delete_api_key_from_keyring("CUSTOM_API_KEY")

    assert deleted == [(service, "CUSTOM_API_KEY") for service in _ALL_SERVICES]
    # Cache entry dropped: the next read consults the backend.
    monkeypatch.setattr(keyring, "get_password", lambda service, username: None)
    assert get_api_key_from_keyring("CUSTOM_API_KEY") is None


def test_delete_forgets_cache_even_when_backend_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(keyring, "get_password", lambda service, username: "cached")
    assert get_api_key_from_keyring("CUSTOM_API_KEY") == "cached"

    def _unavailable(service: str, username: str) -> None:
        raise KeyringError("no keyring")

    monkeypatch.setattr(keyring, "delete_password", _unavailable)

    with pytest.raises(KeyringError):
        delete_api_key_from_keyring("CUSTOM_API_KEY")

    monkeypatch.setattr(keyring, "get_password", lambda service, username: None)
    assert get_api_key_from_keyring("CUSTOM_API_KEY") is None


def test_delete_attempts_legacy_service_when_current_service_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted: list[tuple[str, str]] = []

    def _delete(service: str, username: str) -> None:
        deleted.append((service, username))
        if service == _CURRENT_SERVICE:
            raise KeyringError("delete failed")

    monkeypatch.setattr(keyring, "delete_password", _delete)

    with pytest.raises(KeyringError):
        delete_api_key_from_keyring("CUSTOM_API_KEY")

    assert deleted == [(service, "CUSTOM_API_KEY") for service in _ALL_SERVICES]


def test_delete_prefers_operation_error_over_missing_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted: list[tuple[str, str]] = []

    def _delete(service: str, username: str) -> None:
        deleted.append((service, username))
        if service == _CURRENT_SERVICE:
            raise PasswordDeleteError()
        if service == _RELEASED_LEGACY_SERVICE:
            raise KeyringError("delete failed")
        raise PasswordDeleteError()

    monkeypatch.setattr(keyring, "delete_password", _delete)

    with pytest.raises(KeyringError, match="delete failed"):
        delete_api_key_from_keyring("CUSTOM_API_KEY")

    assert deleted == [(service, "CUSTOM_API_KEY") for service in _ALL_SERVICES]


def test_delete_ignores_missing_entries_after_successful_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted: list[tuple[str, str]] = []

    def _delete(service: str, username: str) -> None:
        deleted.append((service, username))
        if service != _RELEASED_LEGACY_SERVICE:
            raise PasswordDeleteError()

    monkeypatch.setattr(keyring, "delete_password", _delete)

    delete_api_key_from_keyring("CUSTOM_API_KEY")

    assert deleted == [(service, "CUSTOM_API_KEY") for service in _ALL_SERVICES]


def test_delete_raises_missing_when_all_services_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted: list[tuple[str, str]] = []

    def _delete(service: str, username: str) -> None:
        deleted.append((service, username))
        raise PasswordDeleteError()

    monkeypatch.setattr(keyring, "delete_password", _delete)

    with pytest.raises(PasswordDeleteError):
        delete_api_key_from_keyring("CUSTOM_API_KEY")

    assert deleted == [(service, "CUSTOM_API_KEY") for service in _ALL_SERVICES]


def test_macos_set_recreates_item_with_security_stdin_and_unrestricted_acl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(keyring_utils, "_should_use_macos_security", lambda: True)

    def _run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(keyring_utils.subprocess, "run", _run)

    set_api_key_in_keyring("CUSTOM_API_KEY", "new-key")

    assert len(calls) == 3
    delete_args, delete_kwargs = calls[0]
    assert delete_args == [
        "/usr/bin/security",
        "delete-generic-password",
        "-s",
        _CURRENT_SERVICE,
        "-a",
        "CUSTOM_API_KEY",
    ]
    assert "new-key" not in delete_args
    assert delete_kwargs["input"] is None

    args, kwargs = calls[1]
    assert args == ["/usr/bin/security", "-i"]
    assert kwargs["text"] is True
    assert kwargs["capture_output"] is True
    assert kwargs["check"] is True
    assert "new-key" not in args

    command = kwargs["input"]
    assert isinstance(command, str)
    assert command.endswith("\n")
    assert "add-generic-password" in command
    assert "-A" in command
    assert "-U" not in command
    assert _CURRENT_SERVICE in command
    assert "CUSTOM_API_KEY" in command
    assert "new-key" in command
    assert [call[0] for call in calls[2:]] == [
        [
            "/usr/bin/security",
            "delete-generic-password",
            "-s",
            _RELEASED_LEGACY_SERVICE,
            "-a",
            "CUSTOM_API_KEY",
        ]
    ]


def test_macos_set_ignores_missing_item_before_recreate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(keyring_utils, "_should_use_macos_security", lambda: True)

    def _run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[1] == "delete-generic-password":
            raise subprocess.CalledProcessError(
                44,
                args,
                stderr="The specified item could not be found in the keychain.",
            )
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(keyring_utils.subprocess, "run", _run)

    set_api_key_in_keyring("CUSTOM_API_KEY", "new-key")

    assert calls == [
        [
            "/usr/bin/security",
            "delete-generic-password",
            "-s",
            _CURRENT_SERVICE,
            "-a",
            "CUSTOM_API_KEY",
        ],
        ["/usr/bin/security", "-i"],
        [
            "/usr/bin/security",
            "delete-generic-password",
            "-s",
            _RELEASED_LEGACY_SERVICE,
            "-a",
            "CUSTOM_API_KEY",
        ],
    ]


def test_macos_get_uses_security_find_password(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(keyring_utils, "_should_use_macos_security", lambda: True)

    def _run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout="stored-key\n")

    monkeypatch.setattr(keyring_utils.subprocess, "run", _run)

    assert get_api_key_from_keyring("CUSTOM_API_KEY") == "stored-key"
    assert calls == [
        (
            [
                "/usr/bin/security",
                "find-generic-password",
                "-s",
                _CURRENT_SERVICE,
                "-a",
                "CUSTOM_API_KEY",
                "-w",
            ],
            {"input": None, "text": True, "capture_output": True, "check": True},
        )
    ]


def test_macos_get_raises_when_item_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keyring_utils, "_should_use_macos_security", lambda: True)

    def _run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(
            44, args, stderr="The specified item could not be found in the keychain."
        )

    monkeypatch.setattr(keyring_utils.subprocess, "run", _run)

    with pytest.raises(keyring_utils._PasswordNotFoundError):
        keyring_utils._get_password(_CURRENT_SERVICE, "CUSTOM_API_KEY")


def test_macos_get_checks_legacy_service_when_current_item_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(keyring_utils, "_should_use_macos_security", lambda: True)

    def _run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[1] in {"find-generic-password", "delete-generic-password"} and (
            args[3] == _CURRENT_SERVICE
        ):
            raise subprocess.CalledProcessError(
                44,
                args,
                stderr="The specified item could not be found in the keychain.",
            )
        return subprocess.CompletedProcess(args, 0, stdout="legacy-key\n")

    monkeypatch.setattr(keyring_utils.subprocess, "run", _run)

    assert get_api_key_from_keyring("CUSTOM_API_KEY") == "legacy-key"
    assert calls[:2] == [
        [
            "/usr/bin/security",
            "find-generic-password",
            "-s",
            _CURRENT_SERVICE,
            "-a",
            "CUSTOM_API_KEY",
            "-w",
        ],
        [
            "/usr/bin/security",
            "find-generic-password",
            "-s",
            _RELEASED_LEGACY_SERVICE,
            "-a",
            "CUSTOM_API_KEY",
            "-w",
        ],
    ]


def test_macos_set_wraps_security_failure_and_forgets_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(keyring, "get_password", lambda service, username: "stale")
    assert get_api_key_from_keyring("CUSTOM_API_KEY") == "stale"

    monkeypatch.setattr(keyring_utils, "_should_use_macos_security", lambda: True)

    def _run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[1] == "delete-generic-password":
            return subprocess.CompletedProcess(args, 0)
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(keyring_utils.subprocess, "run", _run)

    with pytest.raises(KeyringError):
        set_api_key_in_keyring("CUSTOM_API_KEY", "new-key")

    monkeypatch.setattr(keyring, "get_password", lambda service, username: None)
    assert get_api_key_from_keyring("CUSTOM_API_KEY") is None
