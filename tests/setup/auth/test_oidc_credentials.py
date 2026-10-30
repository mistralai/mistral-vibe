from __future__ import annotations

import asyncio
import json
from threading import Event, get_ident
import traceback

import keyring
from keyring.errors import (
    KeyringError,
    NoKeyringError,
    PasswordDeleteError,
    PasswordSetError,
)
from pydantic import SecretStr, ValidationError
import pytest

from vibe.setup.auth._oidc_client import OidcClient
from vibe.setup.auth._oidc_config import OidcConfig
from vibe.setup.auth._oidc_credentials import (
    OidcCredentialStore,
    OidcStorageError,
    OidcTokens,
)
from vibe.setup.auth._oidc_service import OidcService

_SERVICE = "ai.mistral.vibe.oidc"
_ENTRY = "cli-oidc:v1:tokens"
_SECRETS = ("access-secret", "refresh-secret", "id-secret")


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[str, str, str]] = []
        self.thread_ids: list[int] = []

    def get_password(self, service: str, username: str) -> str | None:
        self._record("get", service, username)
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._record("set", service, username)
        self.values[service, username] = password

    def delete_password(self, service: str, username: str) -> None:
        self._record("delete", service, username)
        if (service, username) not in self.values:
            raise PasswordDeleteError()
        del self.values[service, username]

    def _record(self, operation: str, service: str, username: str) -> None:
        self.calls.append((operation, service, username))
        self.thread_ids.append(get_ident())


@pytest.fixture(autouse=True)
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> FakeKeyring:
    backend = FakeKeyring()
    monkeypatch.delenv("VIBE_TEST_DISABLE_KEYRING", raising=False)
    monkeypatch.setattr(keyring, "get_password", backend.get_password)
    monkeypatch.setattr(keyring, "set_password", backend.set_password)
    monkeypatch.setattr(keyring, "delete_password", backend.delete_password)
    return backend


@pytest.fixture
def tokens() -> OidcTokens:
    return OidcTokens(
        issuer="https://auth.example.test",
        client_id="vibe-cli",
        audience="https://api.example.test",
        subject="user-123",
        access_token=SecretStr(_SECRETS[0]),
        refresh_token=SecretStr(_SECRETS[1]),
        id_token=SecretStr(_SECRETS[2]),
        expires_at=1_900_000_000.5,
    )


def test_tokens_json_contains_actual_secrets_but_python_and_repr_do_not(tokens):
    payload = json.loads(tokens.model_dump_json())

    assert payload["version"] == 1
    for field, secret in zip(
        ("access_token", "refresh_token", "id_token"), _SECRETS, strict=True
    ):
        assert payload[field] == secret
        assert tokens.model_dump(mode="json")[field] == secret
        assert isinstance(tokens.model_dump()[field], SecretStr)
        assert secret not in repr(tokens)
        assert secret not in str(tokens)
        assert secret not in str(tokens.model_dump())
    assert OidcTokens.model_validate_json(tokens.model_dump_json()) == tokens


@pytest.mark.parametrize("version", [0, 2, "1", None])
def test_tokens_reject_unsupported_versions(tokens, version):
    payload = tokens.model_dump(mode="json")
    payload["version"] = version

    with pytest.raises(ValidationError):
        OidcTokens.model_validate(payload)


def test_tokens_default_version_and_forbid_unknown_fields(tokens):
    payload = tokens.model_dump(mode="json")
    del payload["version"]
    assert OidcTokens.model_validate(payload).version == 1

    payload["unknown"] = "unexpected"
    with pytest.raises(ValidationError):
        OidcTokens.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    [
        "issuer",
        "client_id",
        "audience",
        "subject",
        "access_token",
        "refresh_token",
        "id_token",
        "expires_at",
    ],
)
def test_tokens_require_bundle_fields(tokens, field):
    payload = tokens.model_dump(mode="json")
    del payload[field]

    with pytest.raises(ValidationError):
        OidcTokens.model_validate(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("has_refresh_token", [True, False])
async def test_store_round_trip_and_clear(tokens, fake_keyring, has_refresh_token):
    if not has_refresh_token:
        tokens = tokens.model_copy(update={"refresh_token": None})
    store = OidcCredentialStore()
    assert await store.load() is None

    await store.save(tokens)
    raw = fake_keyring.values[_SERVICE, _ENTRY]
    payload = json.loads(raw)
    assert payload["access_token"] == _SECRETS[0]
    assert payload["refresh_token"] == (_SECRETS[1] if has_refresh_token else None)
    assert payload["id_token"] == _SECRETS[2]
    assert "**********" not in raw
    assert await OidcCredentialStore().load() == tokens

    await store.clear()
    assert await store.load() is None
    await store.clear()
    assert fake_keyring.values == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("write_fails", [False, True])
@pytest.mark.parametrize("cancel_count", [1, 2])
async def test_cancelled_save_waits_for_worker_before_returning(
    tokens, fake_keyring, monkeypatch, write_fails, cancel_count
):
    started = asyncio.Event()
    release = Event()
    finished = Event()
    loop = asyncio.get_running_loop()

    def blocked_write(service, username, password):
        loop.call_soon_threadsafe(started.set)
        release.wait()
        try:
            if write_fails:
                raise KeyringError(" ".join(_SECRETS))
            fake_keyring.set_password(service, username, password)
        finally:
            finished.set()

    monkeypatch.setattr(keyring, "set_password", blocked_write)
    store = OidcCredentialStore()
    task = asyncio.create_task(store.save(tokens, complete_on_cancel=True))
    try:
        await started.wait()
        for _ in range(cancel_count):
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        assert not finished.is_set()
        assert fake_keyring.values == {}

        release.set()
        if write_fails:
            with pytest.raises(OidcStorageError, match="Could not save"):
                await task
        else:
            await task
        assert not task.cancelled()

        assert finished.is_set()
        if write_fails:
            assert fake_keyring.values == {}
        else:
            assert fake_keyring.values[_SERVICE, _ENTRY] == tokens.model_dump_json()
            await store.clear()
            assert fake_keyring.values == {}
            assert fake_keyring.calls == [
                ("set", _SERVICE, _ENTRY),
                ("delete", _SERVICE, _ENTRY),
            ]
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_fails", [False, True])
@pytest.mark.parametrize("cancel_count", [1, 2])
async def test_cancelled_clear_holds_service_lock_until_worker_finishes(
    tokens, fake_keyring, monkeypatch, delete_fails, cancel_count
):
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")
    store = OidcCredentialStore()
    await store.save(tokens)
    service = OidcService(
        OidcClient(
            OidcConfig(
                tokens.issuer,
                tokens.client_id,
                tokens.audience,
                "http://127.0.0.1:8766/callback",
            )
        ),
        store,
    )
    started = asyncio.Event()
    replacement_started = asyncio.Event()
    release = Event()
    finished = Event()
    loop = asyncio.get_running_loop()

    def blocked_delete(keyring_service, username):
        loop.call_soon_threadsafe(started.set)
        release.wait()
        try:
            if delete_fails:
                raise KeyringError(" ".join(_SECRETS))
            fake_keyring.delete_password(keyring_service, username)
        finally:
            finished.set()

    async def save_replacement():
        replacement_started.set()
        async with service._lock:
            assert finished.is_set()
            await store.save(tokens)

    monkeypatch.setattr(keyring, "delete_password", blocked_delete)
    clear_task = asyncio.create_task(service.clear())
    replacement_task = None
    try:
        await started.wait()
        replacement_task = asyncio.create_task(save_replacement())
        await replacement_started.wait()
        for _ in range(cancel_count):
            clear_task.cancel()
            await asyncio.sleep(0)
            assert not clear_task.done()
            assert not replacement_task.done()
            assert service._lock.locked()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await clear_task
        await replacement_task
        assert await store.load() == tokens
        assert finished.is_set()
    finally:
        release.set()
        await asyncio.gather(
            clear_task,
            *([replacement_task] if replacement_task else []),
            return_exceptions=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("nonce", [None, "original-login-nonce"])
async def test_store_persists_optional_nonce(tokens, fake_keyring, nonce):
    tokens = tokens.model_copy(update={"nonce": nonce})
    store = OidcCredentialStore()

    await store.save(tokens)

    assert json.loads(fake_keyring.values[_SERVICE, _ENTRY])["nonce"] == nonce
    assert await store.load() == tokens


@pytest.mark.asyncio
async def test_store_loads_bundle_without_nonce(tokens, fake_keyring):
    payload = tokens.model_dump(mode="json")
    del payload["nonce"]
    fake_keyring.values[_SERVICE, _ENTRY] = json.dumps(payload)

    loaded = await OidcCredentialStore().load()

    assert loaded == tokens
    assert loaded is not None
    assert loaded.nonce is None


@pytest.mark.asyncio
async def test_clear_confirms_already_absent_entry(fake_keyring):
    await OidcCredentialStore().clear()

    assert fake_keyring.calls == [
        ("delete", _SERVICE, _ENTRY),
        ("get", _SERVICE, _ENTRY),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_value", ["tokens", ""])
async def test_clear_delete_error_with_remaining_entry_is_sanitized(
    tokens, fake_keyring, monkeypatch, caplog, stored_value
):
    raw = tokens.model_dump_json() if stored_value == "tokens" else stored_value
    fake_keyring.values[_SERVICE, _ENTRY] = raw

    def fail_delete(service, username):
        # keyring 25.7 macOS.Keyring.delete_password wraps every api.Error.
        fake_keyring._record("delete", service, username)
        raise PasswordDeleteError("Access denied: " + " ".join(_SECRETS))

    monkeypatch.setattr(keyring, "delete_password", fail_delete)

    with pytest.raises(OidcStorageError, match="remove") as exc_info:
        await OidcCredentialStore().clear()

    assert fake_keyring.values[_SERVICE, _ENTRY] == raw
    assert fake_keyring.calls == [
        ("delete", _SERVICE, _ENTRY),
        ("get", _SERVICE, _ENTRY),
    ]
    rendered = "".join(traceback.format_exception(exc_info.value))
    for secret in _SECRETS:
        assert secret not in rendered
        assert secret not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [KeyringError, NoKeyringError, ImportError])
async def test_clear_delete_error_with_failed_verification_is_sanitized(
    tokens, fake_keyring, monkeypatch, caplog, error_type
):
    raw = tokens.model_dump_json()
    fake_keyring.values[_SERVICE, _ENTRY] = raw

    def fail_delete(service, username):
        fake_keyring._record("delete", service, username)
        raise PasswordDeleteError(" ".join(_SECRETS))

    def fail_read(service, username):
        fake_keyring._record("get", service, username)
        raise error_type(" ".join(_SECRETS))

    monkeypatch.setattr(keyring, "delete_password", fail_delete)
    monkeypatch.setattr(keyring, "get_password", fail_read)

    with pytest.raises(OidcStorageError, match="verify") as exc_info:
        await OidcCredentialStore().clear()

    assert fake_keyring.values[_SERVICE, _ENTRY] == raw
    assert fake_keyring.calls == [
        ("delete", _SERVICE, _ENTRY),
        ("get", _SERVICE, _ENTRY),
    ]
    rendered = "".join(traceback.format_exception(exc_info.value))
    for secret in _SECRETS:
        assert secret not in rendered
        assert secret not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", ["clear", "login", "unchanged"])
async def test_conditional_refresh_save_respects_shared_slot(tokens, replacement):
    store = OidcCredentialStore()
    other = OidcCredentialStore()
    await store.save(tokens)
    rotated = tokens.model_copy(update={"refresh_token": SecretStr("rotated-refresh")})
    new_login = tokens.model_copy(update={"subject": "other-account"})
    if replacement == "clear":
        await other.clear()
    elif replacement == "login":
        await other.save(new_login)
    saved = await store.save_if_current(rotated, expected=tokens)
    assert saved is (replacement == "unchanged")
    expected = (
        None
        if replacement == "clear"
        else new_login
        if replacement == "login"
        else rotated
    )
    assert await store.load() == expected


@pytest.mark.asyncio
async def test_other_store_clear_waits_for_conditional_write(
    tokens, fake_keyring, monkeypatch
):
    store = OidcCredentialStore()
    other = OidcCredentialStore()
    await store.save(tokens)
    rotated = tokens.model_copy(update={"refresh_token": SecretStr("rotated-refresh")})
    writing = asyncio.Event()
    clearing = asyncio.Event()
    release = Event()
    loop = asyncio.get_running_loop()
    original_clear = other._clear_locked

    def blocked_write(service, username, raw):
        loop.call_soon_threadsafe(writing.set)
        release.wait()
        fake_keyring.set_password(service, username, raw)

    def record_clear():
        loop.call_soon_threadsafe(clearing.set)
        original_clear()

    monkeypatch.setattr(keyring, "set_password", blocked_write)
    monkeypatch.setattr(other, "_clear_locked", record_clear)
    refresh = asyncio.create_task(store.save_if_current(rotated, expected=tokens))
    clear = None
    try:
        await asyncio.wait_for(writing.wait(), 2)
        clear = asyncio.create_task(other.clear())
        await asyncio.wait_for(clearing.wait(), 2)
        assert not clear.done()
        release.set()
        assert await asyncio.wait_for(refresh, 2)
        await asyncio.wait_for(clear, 2)
        assert await other.load() is None
    finally:
        release.set()
        await asyncio.gather(
            refresh, *([clear] if clear else []), return_exceptions=True
        )


@pytest.mark.asyncio
async def test_store_keeps_slot_in_memory_until_reload(tokens, fake_keyring):
    store = OidcCredentialStore()
    assert await store.load() is None
    await store.save(tokens)
    reads = fake_keyring.calls.count(("get", _SERVICE, _ENTRY))
    assert await OidcCredentialStore().load() == tokens
    assert fake_keyring.calls.count(("get", _SERVICE, _ENTRY)) == reads

    # Another process rotated the slot: only an explicit reload sees it.
    replacement = tokens.model_copy(update={"access_token": SecretStr("new-access")})
    fake_keyring.values[_SERVICE, _ENTRY] = replacement.model_dump_json()
    assert await store.load() == tokens
    assert await store.reload() == replacement
    assert await store.load() == replacement

    await store.clear()
    assert await OidcCredentialStore().load() is None


@pytest.mark.asyncio
async def test_store_only_touches_oidc_slot_and_never_migrates_api_keys(
    tokens, fake_keyring
):
    untouched = {
        (service, entry): "api-key-or-legacy-value"
        for service in ("ai.mistral.vibe", "vibe")
        for entry in ("MISTRAL_API_KEY", "CUSTOM_API_KEY", _ENTRY)
    }
    fake_keyring.values.update(untouched)
    store = OidcCredentialStore()

    assert await store.load() is None
    await store.save(tokens)
    await store.clear()

    assert fake_keyring.values == untouched
    assert fake_keyring.calls == [
        ("get", _SERVICE, _ENTRY),
        ("set", _SERVICE, _ENTRY),
        ("delete", _SERVICE, _ENTRY),
    ]


@pytest.mark.asyncio
async def test_keyring_io_runs_off_event_loop_thread(tokens, fake_keyring):
    event_loop_thread = get_ident()
    store = OidcCredentialStore()

    await store.save(tokens)
    await store.reload()
    await store.clear()

    assert len(fake_keyring.thread_ids) == 3
    assert all(thread_id != event_loop_thread for thread_id in fake_keyring.thread_ids)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["load", "save", "clear"])
@pytest.mark.parametrize(
    "error_type", [KeyringError, NoKeyringError, PasswordSetError, ImportError]
)
async def test_storage_errors_are_sanitized(
    tokens, monkeypatch, caplog, operation, error_type
):
    def fail(*args):
        raise error_type(" ".join(_SECRETS))

    method = {
        "load": "get_password",
        "save": "set_password",
        "clear": "delete_password",
    }
    monkeypatch.setattr(keyring, method[operation], fail)
    store = OidcCredentialStore()

    with pytest.raises(OidcStorageError) as exc_info:
        if operation == "save":
            await store.save(tokens)
        else:
            await getattr(store, operation)()

    error = exc_info.value
    rendered = "".join(traceback.format_exception(error))
    assert error.__cause__ is None
    assert error.__suppress_context__
    for secret in _SECRETS:
        assert secret not in str(error)
        assert secret not in repr(error)
        assert secret not in rendered
        assert secret not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["json", "version", "extra", "missing", "secret"])
async def test_invalid_stored_bundle_raises_sanitized_error(
    tokens, fake_keyring, caplog, invalid
):
    payload = tokens.model_dump(mode="json")
    if invalid == "version":
        payload["version"] = 2
    elif invalid == "extra":
        payload["unexpected"] = _SECRETS[0]
    elif invalid == "missing":
        del payload["issuer"]
    elif invalid == "secret":
        payload["access_token"] = {"secret": _SECRETS[0]}
    raw = " ".join(_SECRETS) if invalid == "json" else json.dumps(payload)
    fake_keyring.values[_SERVICE, _ENTRY] = raw

    with pytest.raises(OidcStorageError, match="invalid") as exc_info:
        await OidcCredentialStore().load()

    rendered = "".join(traceback.format_exception(exc_info.value))
    for secret in _SECRETS:
        assert secret not in rendered
        assert secret not in caplog.text
    assert fake_keyring.values[_SERVICE, _ENTRY] == raw
    assert fake_keyring.calls == [("get", _SERVICE, _ENTRY)]


@pytest.mark.asyncio
async def test_disabled_keyring_skips_reads_and_fails_mutations(
    tokens, fake_keyring, monkeypatch
):
    fake_keyring.values[_SERVICE, _ENTRY] = tokens.model_dump_json()
    original = fake_keyring.values.copy()
    monkeypatch.setenv("VIBE_TEST_DISABLE_KEYRING", "1")
    store = OidcCredentialStore()

    assert await store.load() is None
    with pytest.raises(OidcStorageError, match="disabled"):
        await store.save(tokens)
    with pytest.raises(OidcStorageError, match="disabled"):
        await store.clear()

    assert fake_keyring.calls == []
    assert fake_keyring.values == original


@pytest.mark.asyncio
async def test_failed_write_never_falls_back_to_env_file(tokens, monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_HOME", str(tmp_path))
    monkeypatch.setenv("MISTRAL_API_KEY", "existing-api-key")
    env_file = tmp_path / ".env"
    original = "MISTRAL_API_KEY=existing-api-key\n"
    env_file.write_text(original, encoding="utf-8")

    def fail(*args):
        raise KeyringError(_SECRETS[0])

    monkeypatch.setattr(keyring, "set_password", fail)
    with pytest.raises(OidcStorageError):
        await OidcCredentialStore().save(tokens)

    assert env_file.read_text(encoding="utf-8") == original
    assert sorted(path.name for path in tmp_path.iterdir()) == [".env"]
