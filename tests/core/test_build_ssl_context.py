from __future__ import annotations

import ssl
from unittest.mock import MagicMock, patch

import pytest

from vibe.core.utils.http import build_ssl_context, configure_ssl_context


@pytest.fixture(autouse=True)
def _clear_ssl_cache():
    configure_ssl_context(enable_system_trust_store=False)
    build_ssl_context.cache_clear()
    yield
    configure_ssl_context(enable_system_trust_store=False)
    build_ssl_context.cache_clear()


def test_build_ssl_context_returns_ssl_context():
    ctx = build_ssl_context()
    assert isinstance(ctx, ssl.SSLContext)


def test_build_ssl_context_uses_certifi_by_default(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)

    mock_ctx = MagicMock(spec=ssl.SSLContext)
    with (
        patch("vibe.core.utils.http.certifi.where", return_value="/certifi.pem"),
        patch(
            "vibe.core.utils.http.ssl.create_default_context", return_value=mock_ctx
        ) as create_default_context,
    ):
        build_ssl_context()

    create_default_context.assert_called_once_with(cafile="/certifi.pem")


def test_build_ssl_context_uses_system_trust_store_when_configured(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    configure_ssl_context(enable_system_trust_store=True)

    mock_ctx = MagicMock(spec=ssl.SSLContext)
    with (
        patch(
            "vibe.core.utils.http.truststore.SSLContext", return_value=mock_ctx
        ) as truststore_context,
        patch(
            "vibe.core.utils.http.ssl.create_default_context"
        ) as create_default_context,
    ):
        build_ssl_context()

    truststore_context.assert_called_once_with(ssl.PROTOCOL_TLS_CLIENT)
    create_default_context.assert_not_called()


def test_build_ssl_context_loads_custom_cert_file(monkeypatch, tmp_path):
    cert_file = tmp_path / "custom.pem"
    cert_file.write_text("dummy")
    monkeypatch.setenv("SSL_CERT_FILE", str(cert_file))
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)

    mock_ctx = MagicMock(spec=ssl.SSLContext)
    with patch(
        "vibe.core.utils.http.ssl.create_default_context", return_value=mock_ctx
    ):
        build_ssl_context()

    mock_ctx.load_verify_locations.assert_called_once_with(
        cafile=str(cert_file), capath=None
    )


def test_build_ssl_context_loads_custom_cert_file_with_system_trust_store(
    monkeypatch, tmp_path
):
    cert_file = tmp_path / "custom.pem"
    cert_file.write_text("dummy")
    monkeypatch.setenv("SSL_CERT_FILE", str(cert_file))
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    configure_ssl_context(enable_system_trust_store=True)

    mock_ctx = MagicMock(spec=ssl.SSLContext)
    with patch("vibe.core.utils.http.truststore.SSLContext", return_value=mock_ctx):
        build_ssl_context()

    mock_ctx.load_verify_locations.assert_called_once_with(
        cafile=str(cert_file), capath=None
    )


def test_build_ssl_context_loads_custom_cert_dir(monkeypatch, tmp_path):
    cert_dir = tmp_path / "certs"
    cert_dir.mkdir()
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setenv("SSL_CERT_DIR", str(cert_dir))

    mock_ctx = MagicMock(spec=ssl.SSLContext)
    with patch(
        "vibe.core.utils.http.ssl.create_default_context", return_value=mock_ctx
    ):
        build_ssl_context()

    mock_ctx.load_verify_locations.assert_called_once_with(
        cafile=None, capath=str(cert_dir)
    )


def test_build_ssl_context_warns_on_invalid_cert(monkeypatch, caplog):
    monkeypatch.setenv("SSL_CERT_FILE", "/nonexistent/path/cert.pem")
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)

    ctx = build_ssl_context()
    assert isinstance(ctx, ssl.SSLContext)
    assert any(
        "Failed to load custom SSL certificates" in r.message for r in caplog.records
    )


def test_build_ssl_context_no_custom_certs(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)

    mock_ctx = MagicMock(spec=ssl.SSLContext)
    with patch(
        "vibe.core.utils.http.ssl.create_default_context", return_value=mock_ctx
    ):
        build_ssl_context()

    mock_ctx.load_verify_locations.assert_not_called()
