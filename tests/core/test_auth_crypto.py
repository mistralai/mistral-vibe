from __future__ import annotations

from dataclasses import asdict

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from vibe.core.auth import EncryptedPayload, decrypt, encrypt


def _generate_test_key_pair() -> tuple[bytes, bytes]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


class TestEncryptDecrypt:
    def test_encrypt_decrypt_roundtrip(self) -> None:
        private_pem, public_pem = _generate_test_key_pair()

        plaintext = "ghp_test_token_12345"
        encrypted = encrypt(plaintext, public_pem)

        assert encrypted.encrypted_key != plaintext
        assert encrypted.ciphertext != plaintext

        decrypted = decrypt(encrypted, private_pem)
        assert decrypted == plaintext

    def test_encrypted_payload_serialization(self) -> None:
        payload = EncryptedPayload(
            encrypted_key="enc_key", nonce="nonce123", ciphertext="cipher"
        )

        data = asdict(payload)
        restored = EncryptedPayload(**data)

        assert restored == payload
