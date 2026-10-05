"""``providerAuth/read``: the redacted active-provider snapshot behind ``/status``.

The builder is exercised directly with crafted environments so the view and
the API-base redaction are covered without touching a real keyring or
``.env`` file.
"""

from __future__ import annotations

import socket

import pytest

from tests.conftest import build_test_vibe_config
from vibe.app_server._provider_auth import (
    build_provider_auth_view,
    read_provider_auth,
    sanitize_api_base,
)
from vibe.app_server.protocol import ProviderAuthReadResponse
from vibe.app_server.provider_auth import ProviderAuthView
from vibe.core.config import ModelConfig, ProviderConfig, VibeConfigSchema
from vibe.core.types import Backend

_API_KEY_ENV_VAR = "ANTHROPIC_API_KEY"


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


def _view(
    config: VibeConfigSchema, *, environ: dict[str, str] | None = None
) -> ProviderAuthView:
    return build_provider_auth_view(config, environ=environ or {})


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
    def test_reports_model_provider_and_sanitized_base(self) -> None:
        view = _view(_api_key_config(), environ={_API_KEY_ENV_VAR: "env-key"})

        assert view.model_display_name == "Claude Sonnet"
        assert view.provider_name == "anthropic"
        assert view.api_base == "https://api.anthropic.com/v1"

    def test_display_name_falls_back_to_alias(self) -> None:
        view = _view(_api_key_config(display_name=None))

        assert view.model_display_name == "claude-sonnet"

    def test_credential_value_in_api_base_is_redacted(self) -> None:
        view = _view(
            _api_key_config(api_base="https://api.example.com/v1/env-key/models"),
            environ={_API_KEY_ENV_VAR: "env-key"},
        )

        assert view.api_base == "https://api.example.com/v1/[redacted]/models"

    def test_invalid_api_base_is_none(self) -> None:
        view = _view(
            _api_key_config(api_base="not a url"), environ={_API_KEY_ENV_VAR: "env-key"}
        )

        assert view.api_base is None

    def test_keyring_only_credential_is_not_redacted(self) -> None:
        """Only the environment credential is collected for redaction.

        Reading the keyring can block, so the read stays synchronous and a
        key stored only there is not part of the redaction set.
        """
        view = _view(
            _api_key_config(api_base="https://api.example.com/v1/ring-key"), environ={}
        )

        assert view.api_base == "https://api.example.com/v1/ring-key"


class TestReadProviderAuth:
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

        view = await read_provider_auth(_api_key_config())

        assert view.model_display_name == "Claude Sonnet"
        assert view.provider_name == "anthropic"
        assert view.api_base == "https://api.anthropic.com/v1"
        assert "env-key" not in view.model_dump_json()


class TestWireModel:
    def test_response_serializes_camel_case(self) -> None:
        response = ProviderAuthReadResponse(
            auth=ProviderAuthView(
                model_display_name="Claude Sonnet",
                provider_name="anthropic",
                api_base="https://api.anthropic.com/v1",
            )
        )

        assert response.model_dump(mode="json") == {
            "auth": {
                "modelDisplayName": "Claude Sonnet",
                "providerName": "anthropic",
                "apiBase": "https://api.anthropic.com/v1",
            }
        }
