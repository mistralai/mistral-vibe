from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_AES_KEY_SIZE = 32
_NONCE_SIZE = 12
_MIN_RSA_KEY_SIZE = 2048
_MAX_ENCRYPTED_KEY_SIZE = 1024
_MAX_CIPHERTEXT_SIZE = 2 * 1024 * 1024  # Workflow transport limit: 2MB
_PAYLOAD_VERSION = 1
_ALG = "RSA-OAEP-SHA256"
_ENC = "A256GCM"


@dataclass(frozen=True)
class EncryptedPayload:
    encrypted_key: str
    nonce: str
    ciphertext: str
    version: int | None = None
    alg: str | None = None
    enc: str | None = None
    kid: str | None = None
    purpose: str | None = None


def _b64decode_strict(value: str, field_name: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"Invalid base64 for {field_name}") from exc


def _validate_payload_lengths(
    encrypted_key: bytes, nonce: bytes, ciphertext: bytes
) -> None:
    if len(encrypted_key) > _MAX_ENCRYPTED_KEY_SIZE:
        raise ValueError("Encrypted key too large")
    if len(nonce) != _NONCE_SIZE:
        raise ValueError("Invalid nonce size")
    if not ciphertext:
        raise ValueError("Ciphertext is empty")
    if len(ciphertext) > _MAX_CIPHERTEXT_SIZE:
        raise ValueError("Ciphertext exceeds maximum allowed size")


def _build_aad(payload: EncryptedPayload) -> bytes | None:
    if payload.version is None or payload.version <= 0:
        return None
    alg = payload.alg or _ALG
    enc = payload.enc or _ENC
    parts = [f"v={payload.version}", f"alg={alg}", f"enc={enc}"]
    if payload.kid:
        parts.append(f"kid={payload.kid}")
    if payload.purpose:
        parts.append(f"purpose={payload.purpose}")
    return "|".join(parts).encode("utf-8")


def encrypt(plaintext: str, public_key_pem: bytes) -> EncryptedPayload:
    public_key = serialization.load_pem_public_key(public_key_pem)
    if not isinstance(public_key, RSAPublicKey):
        raise TypeError("Expected RSA public key")

    if public_key.key_size < _MIN_RSA_KEY_SIZE:
        raise ValueError(f"RSA key size must be at least {_MIN_RSA_KEY_SIZE} bits")

    aes_key = os.urandom(_AES_KEY_SIZE)
    nonce = os.urandom(_NONCE_SIZE)

    payload = EncryptedPayload(
        encrypted_key="",
        nonce="",
        ciphertext="",
        version=_PAYLOAD_VERSION,
        alg=_ALG,
        enc=_ENC,
    )
    aad = _build_aad(payload)
    aesgcm = AESGCM(aes_key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), aad)

    encrypted_key = public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )

    return EncryptedPayload(
        encrypted_key=base64.b64encode(encrypted_key).decode("ascii"),
        nonce=base64.b64encode(nonce).decode("ascii"),
        ciphertext=base64.b64encode(ciphertext).decode("ascii"),
        version=payload.version,
        alg=payload.alg,
        enc=payload.enc,
    )


def decrypt(payload: EncryptedPayload, private_key_pem: bytes) -> str:
    private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    if not isinstance(private_key, RSAPrivateKey):
        raise TypeError("Expected RSA private key")

    if private_key.key_size < _MIN_RSA_KEY_SIZE:
        raise ValueError(f"RSA key size must be at least {_MIN_RSA_KEY_SIZE} bits")

    encrypted_key = _b64decode_strict(payload.encrypted_key, "encrypted_key")
    nonce = _b64decode_strict(payload.nonce, "nonce")
    ciphertext = _b64decode_strict(payload.ciphertext, "ciphertext")
    _validate_payload_lengths(encrypted_key, nonce, ciphertext)

    aes_key = private_key.decrypt(
        encrypted_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )

    if len(aes_key) != _AES_KEY_SIZE:
        raise ValueError("Invalid AES key size after decryption")

    aesgcm = AESGCM(aes_key)
    aad = _build_aad(payload)
    return aesgcm.decrypt(nonce, ciphertext, aad).decode("utf-8")
