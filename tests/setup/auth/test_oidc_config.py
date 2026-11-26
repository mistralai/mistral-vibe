from __future__ import annotations

import os

import pytest

from vibe.setup.auth._oidc_config import OidcConfig, OidcError, oidc_enabled


@pytest.fixture(autouse=True)
def clean_oidc_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith("VIBE_CLI_OIDC_"):
            monkeypatch.delenv(name)


def test_flag_alone_selects_dedicated_production_registration(monkeypatch):
    # Producer: sre-infra PR #10140, docs/runbooks/vibe-oidc/vibe-code-cli-production.json.
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")
    config = OidcConfig.from_env()
    assert config.issuer == "https://auth.mistral.ai"
    assert config.client_id == "829da7e7-628f-4795-829a-8cca609c76d4"
    assert config.audience == "https://api.mistral.ai"
    assert config.web_origins == (
        "https://chat.mistral.ai",
        "https://console.mistral.ai",
    )


def test_staging_issuer_selects_dedicated_staging_registration(monkeypatch):
    # Producer: sre-infra PR #10140, docs/runbooks/vibe-oidc/vibe-code-cli-staging.json.
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")
    monkeypatch.setenv("VIBE_CLI_OIDC_ISSUER", "https://auth.globalaegis.net")
    config = OidcConfig.from_env()
    assert config.issuer == "https://auth.globalaegis.net"
    assert config.client_id == "3484b11e-2029-4a52-b289-dc826fc03c00"
    assert config.audience == "https://api.globalaegis.net"
    assert config.web_origins == (
        "https://chat.globalaegis.net",
        "https://console.globalaegis.net",
    )


def test_explicit_registration_overrides_remain_available(monkeypatch):
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")
    monkeypatch.setenv("VIBE_CLI_OIDC_CLIENT_ID", "another-cli")
    monkeypatch.setenv("VIBE_CLI_OIDC_AUDIENCE", "")
    config = OidcConfig.from_env()
    assert config.client_id == "another-cli"
    assert config.audience == ""


@pytest.mark.parametrize(
    "audience", ["http://api.mistral.ai", "https://user:pw@api.mistral.ai"]
)
def test_audience_receiving_id_tokens_must_be_https(monkeypatch, audience):
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")
    monkeypatch.setenv("VIBE_CLI_OIDC_AUDIENCE", audience)
    with pytest.raises(OidcError, match="HTTPS"):
        OidcConfig.from_env()


def test_loopback_audience_may_use_http_for_local_testing(monkeypatch):
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")
    monkeypatch.setenv("VIBE_CLI_OIDC_AUDIENCE", "http://127.0.0.1:8000")
    assert OidcConfig.from_env().audience == "http://127.0.0.1:8000"


def test_unknown_issuer_requires_explicit_client_id(monkeypatch):
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")
    monkeypatch.setenv("VIBE_CLI_OIDC_ISSUER", "https://issuer.example")
    with pytest.raises(OidcError, match="CLIENT_ID"):
        OidcConfig.from_env()
    monkeypatch.setenv("VIBE_CLI_OIDC_CLIENT_ID", "custom-client")
    assert OidcConfig.from_env().audience == ""


def test_defaults_do_not_enable_experiment():
    assert oidc_enabled() is False
    with pytest.raises(OidcError, match="disabled"):
        OidcConfig.from_env()
