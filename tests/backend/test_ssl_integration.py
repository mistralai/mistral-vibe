from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Address
from pathlib import Path
import ssl

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
import pytest

from tests.e2e.mock_server import StreamingMockServer
from vibe.core.config import ModelConfig, ProviderConfig
from vibe.core.llm.backend.generic import GenericBackend
from vibe.core.types import Backend, LLMMessage, Role
from vibe.core.utils import build_ssl_context, configure_ssl_context


@dataclass(frozen=True)
class _TLSMaterial:
    ca_file: str
    cert_file: str
    key_file: str


@dataclass(frozen=True)
class _HttpsStreamingMockServer:
    server: StreamingMockServer
    ca_file: str


@pytest.fixture
def https_streaming_mock_server(tmp_path: Path) -> Iterator[_HttpsStreamingMockServer]:
    tls = _write_tls_material(tmp_path)
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain(certfile=tls.cert_file, keyfile=tls.key_file)
    server = StreamingMockServer(ssl_context=ssl_context)
    server.start()
    try:
        yield _HttpsStreamingMockServer(server=server, ca_file=tls.ca_file)
    finally:
        server.stop()


@pytest.mark.asyncio
async def test_generic_backend_streaming_uses_ssl_cert_file(
    monkeypatch: pytest.MonkeyPatch,
    https_streaming_mock_server: _HttpsStreamingMockServer,
) -> None:
    monkeypatch.setenv("SSL_CERT_FILE", https_streaming_mock_server.ca_file)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    configure_ssl_context(enable_system_trust_store=False)
    build_ssl_context.cache_clear()

    chunks = []
    try:
        provider = ProviderConfig(
            name="mock-provider",
            api_base=https_streaming_mock_server.server.api_base,
            api_key_env_var="MISTRAL_API_KEY",
            backend=Backend.GENERIC,
        )
        model = ModelConfig(
            name="mock-model", provider="mock-provider", alias="mock-model"
        )

        async with GenericBackend(provider=provider, timeout=5.0) as backend:
            chunks = [
                chunk
                async for chunk in backend.complete_streaming(
                    model=model, messages=[LLMMessage(role=Role.user, content="Greet")]
                )
            ]
    finally:
        configure_ssl_context(enable_system_trust_store=False)
        build_ssl_context.cache_clear()

    content = "".join(chunk.message.content or "" for chunk in chunks)
    request_payload = https_streaming_mock_server.server.requests[-1]
    assert content == "Hello from mock server"
    assert request_payload.get("stream") is True
    assert request_payload.get("model") == "mock-model"


def _write_tls_material(tmp_path: Path) -> _TLSMaterial:
    now = datetime.now(UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Vibe test CA")])
    ca_cert = (
        x509
        .CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )

    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    server_cert = (
        x509
        .CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_cert.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(IPv4Address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )

    ca_file = tmp_path / "ca.pem"
    cert_file = tmp_path / "server.pem"
    key_file = tmp_path / "server.key"
    ca_file.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    cert_file.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        server_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return _TLSMaterial(
        ca_file=str(ca_file), cert_file=str(cert_file), key_file=str(key_file)
    )
