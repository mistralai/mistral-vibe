"""Tests for SSL/TLS certificate configuration in GenericBackend."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from vibe.core.config import ProviderConfig
from vibe.core.llm.backend.generic import GenericBackend


class TestSSLConfiguration:
    """Test SSL configuration resolution in GenericBackend."""

    def test_ssl_verify_default_is_true(self) -> None:
        """Default SSL verification should be enabled."""
        provider = ProviderConfig(
            name="test",
            api_base="https://example.com/v1",
            api_key_env_var="TEST_KEY",
        )
        backend = GenericBackend(provider=provider)
        assert backend._get_ssl_verify() is True

    def test_ssl_verify_false_disables_verification(self) -> None:
        """ssl_verify=False should disable SSL verification."""
        provider = ProviderConfig(
            name="test",
            api_base="https://example.com/v1",
            api_key_env_var="TEST_KEY",
            ssl_verify=False,
        )
        backend = GenericBackend(provider=provider)
        assert backend._get_ssl_verify() is False

    def test_ssl_cert_path_returns_path(self, tmp_path: Path) -> None:
        """ssl_cert_path should return the resolved certificate path."""
        cert_file = tmp_path / "ca.pem"
        cert_file.write_text("-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----")

        provider = ProviderConfig(
            name="test",
            api_base="https://example.com/v1",
            api_key_env_var="TEST_KEY",
            ssl_cert_path=str(cert_file),
        )
        backend = GenericBackend(provider=provider)
        result = backend._get_ssl_verify()
        assert result == str(cert_file.resolve())

    def test_ssl_cert_path_validation_fails_for_missing_file(self) -> None:
        """Validation should fail if certificate file doesn't exist."""
        with pytest.raises(ValueError, match="SSL certificate file not found"):
            ProviderConfig(
                name="test",
                api_base="https://example.com/v1",
                api_key_env_var="TEST_KEY",
                ssl_cert_path="/nonexistent/path/to/cert.pem",
            )

    def test_ssl_cert_file_env_var_fallback(self, tmp_path: Path) -> None:
        """SSL_CERT_FILE env var should be used as fallback."""
        cert_file = tmp_path / "ca.pem"
        cert_file.write_text("-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----")

        provider = ProviderConfig(
            name="test",
            api_base="https://example.com/v1",
            api_key_env_var="TEST_KEY",
        )
        backend = GenericBackend(provider=provider)

        with patch.dict(os.environ, {"SSL_CERT_FILE": str(cert_file)}, clear=False):
            result = backend._get_ssl_verify()
            assert result == str(cert_file)

    def test_requests_ca_bundle_env_var_fallback(self, tmp_path: Path) -> None:
        """REQUESTS_CA_BUNDLE env var should be used as fallback."""
        cert_file = tmp_path / "ca.pem"
        cert_file.write_text("-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----")

        provider = ProviderConfig(
            name="test",
            api_base="https://example.com/v1",
            api_key_env_var="TEST_KEY",
        )
        backend = GenericBackend(provider=provider)

        # Clear SSL_CERT_FILE to test REQUESTS_CA_BUNDLE
        env_patch = {"REQUESTS_CA_BUNDLE": str(cert_file), "SSL_CERT_FILE": ""}
        with patch.dict(os.environ, env_patch, clear=False):
            result = backend._get_ssl_verify()
            assert result == str(cert_file)

    def test_ssl_cert_file_takes_priority_over_requests_ca_bundle(
        self, tmp_path: Path
    ) -> None:
        """SSL_CERT_FILE should take priority over REQUESTS_CA_BUNDLE."""
        cert_file1 = tmp_path / "ssl_cert.pem"
        cert_file1.write_text("-----BEGIN CERTIFICATE-----\ntest1\n-----END CERTIFICATE-----")
        cert_file2 = tmp_path / "requests_ca.pem"
        cert_file2.write_text("-----BEGIN CERTIFICATE-----\ntest2\n-----END CERTIFICATE-----")

        provider = ProviderConfig(
            name="test",
            api_base="https://example.com/v1",
            api_key_env_var="TEST_KEY",
        )
        backend = GenericBackend(provider=provider)

        env_patch = {
            "SSL_CERT_FILE": str(cert_file1),
            "REQUESTS_CA_BUNDLE": str(cert_file2),
        }
        with patch.dict(os.environ, env_patch, clear=False):
            result = backend._get_ssl_verify()
            assert result == str(cert_file1)

    def test_explicit_config_takes_priority_over_env_vars(self, tmp_path: Path) -> None:
        """Explicit ssl_cert_path should take priority over env vars."""
        cert_file1 = tmp_path / "explicit.pem"
        cert_file1.write_text("-----BEGIN CERTIFICATE-----\ntest1\n-----END CERTIFICATE-----")
        cert_file2 = tmp_path / "env.pem"
        cert_file2.write_text("-----BEGIN CERTIFICATE-----\ntest2\n-----END CERTIFICATE-----")

        provider = ProviderConfig(
            name="test",
            api_base="https://example.com/v1",
            api_key_env_var="TEST_KEY",
            ssl_cert_path=str(cert_file1),
        )
        backend = GenericBackend(provider=provider)

        with patch.dict(os.environ, {"SSL_CERT_FILE": str(cert_file2)}, clear=False):
            result = backend._get_ssl_verify()
            # Should use explicit config, not env var
            assert str(cert_file1.resolve()) in result

    def test_ssl_verify_false_takes_priority_over_cert_path(self, tmp_path: Path) -> None:
        """ssl_verify=False should take priority over ssl_cert_path."""
        cert_file = tmp_path / "ca.pem"
        cert_file.write_text("-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----")

        provider = ProviderConfig(
            name="test",
            api_base="https://example.com/v1",
            api_key_env_var="TEST_KEY",
            ssl_cert_path=str(cert_file),
            ssl_verify=False,
        )
        backend = GenericBackend(provider=provider)
        assert backend._get_ssl_verify() is False

    def test_missing_env_cert_file_falls_back_to_true(self) -> None:
        """If env var points to non-existent file, should fall back to True."""
        provider = ProviderConfig(
            name="test",
            api_base="https://example.com/v1",
            api_key_env_var="TEST_KEY",
        )
        backend = GenericBackend(provider=provider)

        env_patch = {"SSL_CERT_FILE": "/nonexistent/cert.pem", "REQUESTS_CA_BUNDLE": ""}
        with patch.dict(os.environ, env_patch, clear=False):
            result = backend._get_ssl_verify()
            assert result is True
