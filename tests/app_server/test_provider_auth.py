"""``providerAuth/read``: the redacted active-provider snapshot behind ``/status``.

The builder is exercised directly with crafted probes so every row of the
behavior table is covered without touching a real keyring or ``.env`` file;
the async wrapper's deadline and the API-base redaction get their own tests.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
import os
from pathlib import Path
import socket
import threading

import keyring
from keyring.errors import KeyringError
import pytest

from tests.conftest import build_test_vibe_config
import vibe.app_server._provider_auth as provider_auth_module
from vibe.app_server._provider_auth import (
    SourceProbe,
    build_provider_auth_view,
    read_provider_auth,
    sanitize_api_base,
)
from vibe.app_server.protocol import ProviderAuthReadResponse
from vibe.app_server.provider_auth import (
    ProviderAuthKind,
    ProviderAuthView,
    ProviderCredentialSourceKind,
    ProviderCredentialSourceState,
    ProviderCredentialSourceView,
)
from vibe.core.config import (
    EnvValueOrigin,
    ModelConfig,
    ProviderConfig,
    VibeConfigSchema,
    env_value_origin,
    record_env_value_origin,
)
from vibe.core.types import Backend
import vibe.setup.auth.api_key_persistence as api_key_persistence
from vibe.setup.auth.api_key_persistence import persist_api_key
from vibe.utils.api_keys import ApiKeySource, resolve_api_key_with_origin

_API_KEY_ENV_VAR = "ANTHROPIC_API_KEY"


@pytest.fixture(autouse=True)
def _restore_env_value_origins() -> Iterator[None]:
    """The origin record is process-global; a test that records a key must
    not leave it behind for whichever test reads the record next.
    """
    import vibe.core.config.vibe_schema as vibe_schema

    snapshot = dict(vibe_schema._ENV_VALUE_ORIGINS)
    yield
    vibe_schema._ENV_VALUE_ORIGINS.clear()
    vibe_schema._ENV_VALUE_ORIGINS.update(snapshot)


@pytest.fixture(autouse=True)
def _fresh_probe_permits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test gets its own permits.

    A test that abandons a hanging probe holds its permit until the probe
    answers, and that process-global hold must not decide the next test's
    verdict.
    """
    monkeypatch.setattr(
        provider_auth_module, "_PROBE_PERMITS", threading.BoundedSemaphore(4)
    )


def _api_key_config(
    *,
    api_base: str = "https://api.anthropic.com/v1",
    display_name: str | None = "Claude Sonnet",
) -> VibeConfigSchema:
    provider = ProviderConfig(
        name="anthropic",
        api_base=api_base,
        api_key_env_var=_API_KEY_ENV_VAR,
        backend=Backend.GENERIC,
    )
    model = ModelConfig(
        name="claude-sonnet",
        provider="anthropic",
        alias="claude-sonnet",
        display_name=display_name,
    )
    return build_test_vibe_config(
        providers=[provider], models=[model], active_model="claude-sonnet"
    )


def _no_auth_config() -> VibeConfigSchema:
    provider = ProviderConfig(
        name="llamacpp",
        api_base="http://127.0.0.1:8080/v1",
        api_key_env_var="",
        backend=Backend.GENERIC,
    )
    model = ModelConfig(
        name="devstral-local", provider="llamacpp", alias="devstral-local"
    )
    return build_test_vibe_config(
        providers=[provider], models=[model], active_model="devstral-local"
    )


def _vertex_config(
    *,
    api_key_env_var: str = "",
    api_base: str = "https://us-central1-aiplatform.googleapis.com/v1",
) -> VibeConfigSchema:
    provider = ProviderConfig(
        name="vertex",
        api_base=api_base,
        api_key_env_var=api_key_env_var,
        api_style="vertex-anthropic",
        backend=Backend.GENERIC,
        project_id="a-project",
        region="us-central1",
    )
    model = ModelConfig(name="claude-vertex", provider="vertex", alias="claude-vertex")
    return build_test_vibe_config(
        providers=[provider], models=[model], active_model="claude-vertex"
    )


def _view(
    config: VibeConfigSchema,
    *,
    environ: dict[str, str] | None = None,
    origin_of: Callable[[str], EnvValueOrigin | None] | None = None,
    env_file: SourceProbe | None = None,
    keyring: SourceProbe | None = None,
) -> ProviderAuthView:
    model = config.get_active_model()
    provider = config.get_provider_for_model(model)
    return build_provider_auth_view(
        model,
        provider,
        environ=environ or {},
        origin_of=origin_of,
        env_file_probe=env_file or SourceProbe(),
        keyring_probe=keyring or SourceProbe(),
    )


def _kinds(view: ProviderAuthView) -> list[tuple[ProviderCredentialSourceKind, str]]:
    rendered = []
    for source in view.sources:
        marker = (
            "in_use"
            if source.state is ProviderCredentialSourceState.IN_USE
            else "unavailable"
            if source.state is ProviderCredentialSourceState.UNAVAILABLE
            else "present"
        )
        rendered.append((source.kind, marker))
    return rendered


class TestSanitizeApiBase:
    def test_keeps_scheme_host_port_and_path(self) -> None:
        assert (
            sanitize_api_base("https://api.example.com:8443/v1", [])
            == "https://api.example.com:8443/v1"
        )

    def test_drops_userinfo_query_and_fragment(self) -> None:
        assert (
            sanitize_api_base(
                "https://user:pass@api.example.com/v1?key=abc#frag", ["secret"]
            )
            == "https://api.example.com/v1"
        )

    def test_redacts_raw_secret_in_path(self) -> None:
        assert (
            sanitize_api_base("https://api.example.com/v1/KEY123/x", ["KEY123"])
            == "https://api.example.com/v1/[redacted]/x"
        )

    def test_keeps_ipv6_host_bracketed_with_port(self) -> None:
        assert (
            sanitize_api_base("https://[2001:db8::1]:8443/v1", [])
            == "https://[2001:db8::1]:8443/v1"
        )

    def test_redacts_url_encoded_secret(self) -> None:
        assert (
            sanitize_api_base("https://api.example.com/v1/a%20b/c", ["a b"])
            == "https://api.example.com/v1/[redacted]/c"
        )

    def test_redacts_lowercase_percent_encoded_secret(self) -> None:
        # Percent-encoding hex is case-insensitive (RFC 3986) while ``quote``
        # emits uppercase, so a lowercase-encoded secret must redact too.
        assert (
            sanitize_api_base("https://api.example.com/v1/sk%2fkey", ["sk/key"])
            == "https://api.example.com/v1/[redacted]"
        )

    def test_redacts_a_mixed_encoding_of_the_secret(self) -> None:
        # A pasted base can encode some characters and leave others bare.
        assert (
            sanitize_api_base("https://api.example.com/v1/a%2Fb+c/x", ["a/b+c"])
            == "https://api.example.com/v1/[redacted]/x"
        )

    def test_redacts_percent_encoded_unreserved_characters(self) -> None:
        # ``quote`` leaves ``-`` and ``~`` bare, but a pasted base may still
        # percent-encode them.
        assert (
            sanitize_api_base("https://api.example.com/v1/key%2D123", ["key-123"])
            == "https://api.example.com/v1/[redacted]"
        )
        assert (
            sanitize_api_base("https://api.example.com/v1/key%7E123", ["key~123"])
            == "https://api.example.com/v1/[redacted]"
        )

    def test_redacts_non_ascii_secret_in_utf8_percent_encoding(self) -> None:
        assert (
            sanitize_api_base("https://api.example.com/v1/cl%C3%A9/x", ["clé"])
            == "https://api.example.com/v1/[redacted]/x"
        )

    def test_redacts_form_style_plus_for_a_space(self) -> None:
        assert (
            sanitize_api_base("https://api.example.com/v1/a+b/x", ["a b"])
            == "https://api.example.com/v1/[redacted]/x"
        )

    def test_redacts_the_longest_secret_whole_when_one_contains_another(self) -> None:
        # A short secret that is a substring of a longer one must not split
        # the longer one's occurrences and leave the remainder on screen.
        assert (
            sanitize_api_base(
                "https://api.example.com/v1/sk-key-123/x", ["sk", "sk-key-123"]
            )
            == "https://api.example.com/v1/[redacted]/x"
        )

    @pytest.mark.parametrize(
        "api_base",
        [
            "not a url",
            "ftp://api.example.com/v1",
            "http:///no-host",
            "https://api.example.com:not-a-port/v1",
            "https://api.example.com/v1\u0007",
            "https://api.example.com/v1\u0085",
        ],
    )
    def test_invalid_or_unsafe_api_base_is_none(self, api_base: str) -> None:
        assert sanitize_api_base(api_base, []) is None


class TestBuildProviderAuthView:
    def test_process_env_in_use_lists_shadowed_sources_in_precedence_order(
        self,
    ) -> None:
        view = _view(
            _api_key_config(),
            environ={_API_KEY_ENV_VAR: "env-key"},
            env_file=SourceProbe(value="file-key"),
            keyring=SourceProbe(value="keyring-key"),
        )

        assert view.model_display_name == "Claude Sonnet"
        assert view.provider_name == "anthropic"
        assert view.api_base == "https://api.anthropic.com/v1"
        assert view.auth_kind is ProviderAuthKind.API_KEY
        assert view.api_key_env_var == _API_KEY_ENV_VAR
        assert _kinds(view) == [
            (ProviderCredentialSourceKind.PROCESS_ENV, "in_use"),
            (ProviderCredentialSourceKind.VIBE_HOME_ENV_FILE, "present"),
            (ProviderCredentialSourceKind.OS_KEYRING, "present"),
        ]

    def test_injected_dotenv_value_reports_env_file_as_active(self) -> None:
        view = _view(
            _api_key_config(),
            environ={_API_KEY_ENV_VAR: "file-key"},
            origin_of=lambda _key: EnvValueOrigin.VIBE_HOME_ENV_FILE,
            env_file=SourceProbe(value="file-key"),
            keyring=SourceProbe(value="keyring-key"),
        )

        assert _kinds(view) == [
            (ProviderCredentialSourceKind.VIBE_HOME_ENV_FILE, "in_use"),
            (ProviderCredentialSourceKind.OS_KEYRING, "present"),
        ]

    def test_active_env_file_source_never_carries_unavailable(self) -> None:
        """The credential was loaded at startup and serves regardless, so the
        in-use entry reports no `unavailable` flag even when its file cannot
        be re-read. A future edit that starts propagating the flag onto the
        in-use entry must fail here.
        """
        view = _view(
            _api_key_config(),
            environ={_API_KEY_ENV_VAR: "file-key"},
            origin_of=lambda _key: EnvValueOrigin.VIBE_HOME_ENV_FILE,
            env_file=SourceProbe(unavailable=True),
        )

        assert _kinds(view) == [
            (ProviderCredentialSourceKind.VIBE_HOME_ENV_FILE, "in_use")
        ]

    def test_known_active_source_with_unreadable_env_file_marks_it_unavailable(
        self,
    ) -> None:
        view = _view(
            _api_key_config(),
            environ={_API_KEY_ENV_VAR: "env-key"},
            env_file=SourceProbe(unavailable=True),
            keyring=SourceProbe(value="keyring-key"),
        )

        assert _kinds(view) == [
            (ProviderCredentialSourceKind.PROCESS_ENV, "in_use"),
            (ProviderCredentialSourceKind.VIBE_HOME_ENV_FILE, "unavailable"),
            (ProviderCredentialSourceKind.OS_KEYRING, "present"),
        ]

    def test_keyring_active_with_post_startup_env_file_value(self) -> None:
        """A ``.env`` entry added after startup was never injected, so it
        holds no credential the session would send: it lists unmarked.
        """
        view = _view(
            _api_key_config(),
            env_file=SourceProbe(value="file-key"),
            keyring=SourceProbe(value="keyring-key"),
        )

        assert _kinds(view) == [
            (ProviderCredentialSourceKind.OS_KEYRING, "in_use"),
            (ProviderCredentialSourceKind.VIBE_HOME_ENV_FILE, "present"),
        ]

    def test_unreadable_keyring_without_environment_key_lists_it_unavailable(
        self,
    ) -> None:
        view = _view(
            _api_key_config(),
            env_file=SourceProbe(value=None),
            keyring=SourceProbe(unavailable=True),
        )

        assert _kinds(view) == [
            (ProviderCredentialSourceKind.OS_KEYRING, "unavailable")
        ]

    def test_unreadable_keyring_keeps_a_post_startup_env_file_value_listed(
        self,
    ) -> None:
        view = _view(
            _api_key_config(),
            env_file=SourceProbe(value="file-key"),
            keyring=SourceProbe(unavailable=True),
        )

        assert _kinds(view) == [
            (ProviderCredentialSourceKind.VIBE_HOME_ENV_FILE, "present"),
            (ProviderCredentialSourceKind.OS_KEYRING, "unavailable"),
        ]

    def test_every_source_checked_and_empty_is_no_source_available(self) -> None:
        view = _view(_api_key_config())

        assert view.auth_kind is ProviderAuthKind.API_KEY
        assert view.sources == []

    def test_no_auth_provider_requires_no_authentication(self) -> None:
        view = _view(_no_auth_config())

        assert view.auth_kind is ProviderAuthKind.NONE
        assert view.api_key_env_var is None
        assert view.sources == []
        assert view.api_base == "http://127.0.0.1:8080/v1"

    def test_adc_provider_collects_no_probed_values_for_redaction(self) -> None:
        """The negative space of the redaction set: ADC probes nothing, so
        file and keyring values cannot join it — a future "probe for every
        kind" change fails here instead of silently widening /status's
        keyring reads.
        """
        view = _view(
            _vertex_config(
                api_key_env_var=_API_KEY_ENV_VAR,
                api_base="https://example.com/v1/file-key/ring-key",
            ),
            environ={_API_KEY_ENV_VAR: "env-key"},
            env_file=SourceProbe(value="file-key"),
            keyring=SourceProbe(value="ring-key"),
        )

        assert view.api_base == "https://example.com/v1/file-key/ring-key"

    def test_adc_provider_still_redacts_the_environment_credential(self) -> None:
        """ADC never sends the configured key, but a base pasted with it
        still shows credential material: the environment value stays in the
        redaction set for every provider kind.
        """
        view = _view(
            _vertex_config(
                api_key_env_var=_API_KEY_ENV_VAR,
                api_base="https://example.com/v1/env-key/x",
            ),
            environ={_API_KEY_ENV_VAR: "env-key"},
        )

        assert view.auth_kind is ProviderAuthKind.GOOGLE_ADC
        assert view.api_base == "https://example.com/v1/[redacted]/x"
        assert view.sources == []

    def test_vertex_provider_reports_google_adc(self) -> None:
        view = _view(
            _vertex_config(),
            environ={"GOOGLE_ADC": "ignored"},
            keyring=SourceProbe(value="ignored"),
        )

        assert view.auth_kind is ProviderAuthKind.GOOGLE_ADC
        assert view.sources == []

    def test_credential_value_in_api_base_is_redacted(self) -> None:
        view = _view(
            _api_key_config(api_base="https://api.example.com/v1/env-key/models"),
            environ={_API_KEY_ENV_VAR: "env-key"},
        )

        assert view.api_base == "https://api.example.com/v1/[redacted]/models"

    def test_every_source_value_joins_the_redaction_set(self) -> None:
        """A key stored only in the keyring — or only in a post-startup
        ``.env`` entry — is collected for redaction too: bounded source
        inspection is what makes reading them safe.
        """
        view = _view(
            _api_key_config(api_base="https://api.example.com/v1/file-key/ring-key"),
            env_file=SourceProbe(value="file-key"),
            keyring=SourceProbe(value="ring-key"),
        )

        assert view.api_base == "https://api.example.com/v1/[redacted]/[redacted]"

    def test_invalid_api_base_is_none(self) -> None:
        view = _view(
            _api_key_config(api_base="not a url"), environ={_API_KEY_ENV_VAR: "env-key"}
        )

        assert view.api_base is None

    def test_display_name_falls_back_to_alias(self) -> None:
        view = _view(_api_key_config(display_name=None))

        assert view.model_display_name == "claude-sonnet"


class TestPrecedenceContract:
    """The view's in-use marker must agree with what resolution would send.

    ``active_api_key`` states the precedence once and both sides consume it;
    this matrix is the enforcement. A change to resolution that reordered or
    widened the precedence fails here instead of leaving ``/status`` marking
    the wrong source with every other test green.
    """

    @pytest.mark.parametrize(
        ("env_value", "keyring_value"),
        [
            (None, None),
            (None, "keyring-key"),
            ("", "keyring-key"),
            ("env-key", None),
            ("env-key", "keyring-key"),
        ],
    )
    def test_view_active_source_matches_resolution(
        self,
        monkeypatch: pytest.MonkeyPatch,
        env_value: str | None,
        keyring_value: str | None,
    ) -> None:
        if env_value is None:
            monkeypatch.delenv(_API_KEY_ENV_VAR, raising=False)
        else:
            monkeypatch.setenv(_API_KEY_ENV_VAR, env_value)
        monkeypatch.setattr(
            keyring, "get_password", lambda service, username: keyring_value
        )

        resolved = resolve_api_key_with_origin(_API_KEY_ENV_VAR)
        view = _view(
            _api_key_config(),
            environ=dict(os.environ),
            keyring=SourceProbe(value=keyring_value),
        )

        in_use = [
            source
            for source in view.sources
            if source.state is ProviderCredentialSourceState.IN_USE
        ]
        if resolved is None:
            assert in_use == []
            return
        assert len(in_use) == 1
        expected = (
            ProviderCredentialSourceKind.PROCESS_ENV
            if resolved[1].source is ApiKeySource.ENVIRONMENT
            else ProviderCredentialSourceKind.OS_KEYRING
        )
        assert in_use[0].kind is expected


class TestStoredCredentialProvenance:
    """Vibe itself writes credentials into ``os.environ`` — onboarding mirrors
    a stored key there so the running process can use it at once. The view
    must attribute such values to their real source, not to the parent shell.
    """

    def test_stored_keyring_credential_is_not_reported_as_process_env(self) -> None:
        # setup/storeCredential persisted to the keyring and mirrored the
        # key into os.environ: the user exported nothing.
        view = _view(
            _api_key_config(),
            environ={_API_KEY_ENV_VAR: "new-key"},
            keyring=SourceProbe(value="new-key"),
            origin_of=lambda _key: EnvValueOrigin.OS_KEYRING,
        )

        assert _kinds(view) == [(ProviderCredentialSourceKind.OS_KEYRING, "in_use")]

    def test_stored_env_file_fallback_credential_reports_the_env_file(self) -> None:
        # The keyring was unavailable, so persistence fell back to $VIBE_HOME/.env.
        view = _view(
            _api_key_config(),
            environ={_API_KEY_ENV_VAR: "new-key"},
            env_file=SourceProbe(value="new-key"),
            origin_of=lambda _key: EnvValueOrigin.VIBE_HOME_ENV_FILE,
        )

        assert _kinds(view) == [
            (ProviderCredentialSourceKind.VIBE_HOME_ENV_FILE, "in_use")
        ]

    def test_overwritten_stored_credential_reports_the_shell(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The record matches only the exact value Vibe wrote: a shell export
        that replaced it is the parent shell's credential.
        """
        record_env_value_origin(
            _API_KEY_ENV_VAR, "stored-key", EnvValueOrigin.OS_KEYRING
        )
        monkeypatch.setenv(_API_KEY_ENV_VAR, "shell-key")

        assert env_value_origin(_API_KEY_ENV_VAR) is None

        view = _view(
            _api_key_config(),
            environ={_API_KEY_ENV_VAR: "shell-key"},
            origin_of=env_value_origin,
        )

        assert _kinds(view) == [(ProviderCredentialSourceKind.PROCESS_ENV, "in_use")]


class TestStoredCredentialThenStatus:
    """The P0 regression, end to end: onboarding stores a credential
    (mirroring it into ``os.environ``), then ``/status`` must name the real
    source — not the parent shell.
    """

    @pytest.mark.asyncio
    async def test_keyring_store_then_status_reports_the_keyring(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_API_KEY_ENV_VAR, "old-key")
        monkeypatch.setattr(keyring, "set_password", lambda s, u, p: None)
        monkeypatch.setattr(keyring, "delete_password", lambda s, u: None)
        monkeypatch.setattr(
            provider_auth_module,
            "probe_keyring",
            lambda _var: SourceProbe(value="new-key"),
        )
        monkeypatch.setattr(
            provider_auth_module,
            "probe_env_file",
            lambda _path, _var: SourceProbe(value=None),
        )
        config = _api_key_config()
        provider = config.get_provider_for_model(config.get_active_model())

        assert persist_api_key(provider, "new-key") == "completed"
        view = await read_provider_auth(config)

        assert _kinds(view) == [(ProviderCredentialSourceKind.OS_KEYRING, "in_use")]

    @pytest.mark.asyncio
    async def test_env_file_fallback_store_then_status_reports_the_env_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("", encoding="utf-8")
        monkeypatch.setenv(_API_KEY_ENV_VAR, "old-key")
        monkeypatch.setattr(
            api_key_persistence, "GLOBAL_ENV_FILE", type("G", (), {"path": env_file})()
        )

        def _no_backend(service: str, username: str, password: str) -> None:
            raise KeyringError("no backend")

        monkeypatch.setattr(keyring, "set_password", _no_backend)
        monkeypatch.setattr(
            provider_auth_module, "probe_keyring", lambda _var: SourceProbe(value=None)
        )
        monkeypatch.setattr(
            provider_auth_module,
            "probe_env_file",
            lambda _path, _var: SourceProbe(value="new-key"),
        )
        config = _api_key_config()
        provider = config.get_provider_for_model(config.get_active_model())

        assert persist_api_key(provider, "new-key") == "completed"
        view = await read_provider_auth(config)

        assert _kinds(view) == [
            (ProviderCredentialSourceKind.VIBE_HOME_ENV_FILE, "in_use")
        ]


class TestReadProviderAuth:
    @pytest.mark.asyncio
    async def test_reads_process_environment_and_reports_sources(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_API_KEY_ENV_VAR, "env-key")
        monkeypatch.setattr(provider_auth_module, "env_value_origin", lambda _key: None)
        monkeypatch.setattr(
            provider_auth_module, "probe_keyring", lambda _var: SourceProbe(value=None)
        )
        monkeypatch.setattr(
            provider_auth_module,
            "probe_env_file",
            lambda _path, _var: SourceProbe(value=None),
        )

        view = await read_provider_auth(_api_key_config())

        assert _kinds(view) == [(ProviderCredentialSourceKind.PROCESS_ENV, "in_use")]

    @pytest.mark.asyncio
    async def test_adc_provider_skips_source_inspection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ADC resolves on request and names no variable: no source is
        inspected for it either.
        """

        def _fail(*args: object) -> SourceProbe:
            raise AssertionError("no source is inspected for an ADC provider")

        monkeypatch.setattr(provider_auth_module, "probe_keyring", _fail)
        monkeypatch.setattr(provider_auth_module, "probe_env_file", _fail)

        view = await read_provider_auth(_vertex_config())

        assert view.auth_kind is ProviderAuthKind.GOOGLE_ADC

    @pytest.mark.asyncio
    async def test_no_auth_provider_skips_source_inspection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _fail(var: str) -> SourceProbe:
            raise AssertionError("no source is inspected for a no-auth provider")

        monkeypatch.setattr(provider_auth_module, "probe_keyring", _fail)

        view = await read_provider_auth(_no_auth_config())

        assert view.auth_kind is ProviderAuthKind.NONE

    @pytest.mark.asyncio
    async def test_env_file_path_is_the_file_the_probe_reads(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The view carries the server's resolved ``$VIBE_HOME/.env`` path.

        The client renders this path as the source label, so it must be the
        file the probe actually reads — including a relative ``VIBE_HOME``,
        which resolves to an absolute path rather than a cwd-dependent label.
        """
        monkeypatch.setenv("VIBE_HOME", str(tmp_path / "vibe-home"))
        monkeypatch.setattr(provider_auth_module, "env_value_origin", lambda _key: None)
        monkeypatch.setattr(
            provider_auth_module, "probe_keyring", lambda _var: SourceProbe(value=None)
        )
        monkeypatch.setattr(
            provider_auth_module,
            "probe_env_file",
            lambda _path, _var: SourceProbe(value=None),
        )

        view = await read_provider_auth(_api_key_config())

        assert view.env_file_path == str((tmp_path / "vibe-home").resolve() / ".env")

    @pytest.mark.asyncio
    async def test_non_api_key_kinds_carry_no_env_file_path(self) -> None:
        view = await read_provider_auth(_no_auth_config())

        assert view.env_file_path is None

    @pytest.mark.asyncio
    async def test_read_reports_legacy_keyring_value_without_writing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """/status is read-only: a legacy-service keyring entry is reported as
        the in-use source, never migrated by the probe.
        """
        writes: list[tuple[str, str, str]] = []
        deletes: list[tuple[str, str]] = []

        def _legacy_value(service: str, username: str) -> str | None:
            if service == "vibe":
                return "legacy-key"
            return None

        monkeypatch.setattr(keyring, "get_password", _legacy_value)
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
        monkeypatch.delenv(_API_KEY_ENV_VAR, raising=False)
        monkeypatch.setattr(provider_auth_module, "env_value_origin", lambda _key: None)
        monkeypatch.setattr(
            provider_auth_module,
            "probe_env_file",
            lambda _path, _var: SourceProbe(value=None),
        )

        view = await read_provider_auth(_api_key_config())

        assert _kinds(view) == [(ProviderCredentialSourceKind.OS_KEYRING, "in_use")]
        assert writes == []
        assert deletes == []

    @pytest.mark.asyncio
    async def test_read_makes_no_network_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The read sends no provider request and shows no validation state:
        any socket construction during the read fails the test.
        """

        def _no_sockets(*args: object, **kwargs: object) -> object:
            raise AssertionError("providerAuth/read must not open a socket")

        monkeypatch.setattr(socket, "socket", _no_sockets)
        monkeypatch.setenv(_API_KEY_ENV_VAR, "env-key")
        monkeypatch.setattr(provider_auth_module, "env_value_origin", lambda _key: None)
        monkeypatch.setattr(keyring, "get_password", lambda service, username: None)
        monkeypatch.setattr(
            provider_auth_module,
            "probe_env_file",
            lambda _path, _var: SourceProbe(value=None),
        )

        view = await read_provider_auth(_api_key_config())

        assert _kinds(view) == [(ProviderCredentialSourceKind.PROCESS_ENV, "in_use")]
        assert "env-key" not in view.model_dump_json()


class TestWireModel:
    def test_response_serializes_camel_case(self) -> None:
        response = ProviderAuthReadResponse(
            auth=ProviderAuthView(
                model_display_name="Claude Sonnet",
                provider_name="anthropic",
                api_base="https://api.anthropic.com/v1",
                auth_kind=ProviderAuthKind.API_KEY,
                api_key_env_var=_API_KEY_ENV_VAR,
                env_file_path="/home/user/.vibe/.env",
                sources=[],
            )
        )

        assert response.model_dump(mode="json") == {
            "auth": {
                "modelDisplayName": "Claude Sonnet",
                "providerName": "anthropic",
                "apiBase": "https://api.anthropic.com/v1",
                "authKind": "api_key",
                "apiKeyEnvVar": _API_KEY_ENV_VAR,
                "envFilePath": "/home/user/.vibe/.env",
                "sources": [],
            }
        }

    def test_sources_serialize_camel_case(self) -> None:
        """The Rust `· currently in use` marker rides on ``inUse``: a wire
        casing change would silently unmark every source while the Rust unit
        tests (which hand-construct the JSON) stayed green.
        """
        response = ProviderAuthReadResponse(
            auth=ProviderAuthView(
                model_display_name="Claude Sonnet",
                provider_name="anthropic",
                auth_kind=ProviderAuthKind.API_KEY,
                api_key_env_var=_API_KEY_ENV_VAR,
                env_file_path="/home/user/.vibe/.env",
                sources=[
                    ProviderCredentialSourceView(
                        kind=ProviderCredentialSourceKind.PROCESS_ENV,
                        state=ProviderCredentialSourceState.IN_USE,
                    ),
                    ProviderCredentialSourceView(
                        kind=ProviderCredentialSourceKind.OS_KEYRING,
                        state=ProviderCredentialSourceState.UNAVAILABLE,
                    ),
                ],
            )
        )

        assert response.model_dump(mode="json")["auth"]["sources"] == [
            {"kind": "process_env", "state": "in_use"},
            {"kind": "os_keyring", "state": "unavailable"},
        ]
