from __future__ import annotations

import base64
import hashlib
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class EncryptionConfigurationError(RuntimeError):
    pass


def _load_key() -> bytes:
    raw = os.getenv("HEALTHTRACE_FIELD_ENCRYPTION_KEY", "").strip()
    if not raw:
        raise EncryptionConfigurationError(
            "HEALTHTRACE_FIELD_ENCRYPTION_KEY is required for sensitive records"
        )
    try:
        key = base64.urlsafe_b64decode(raw.encode("ascii"))
    except Exception as exc:
        raise EncryptionConfigurationError(
            "HEALTHTRACE_FIELD_ENCRYPTION_KEY must be URL-safe base64"
        ) from exc
    if len(key) != 32:
        raise EncryptionConfigurationError(
            "HEALTHTRACE_FIELD_ENCRYPTION_KEY must decode to exactly 32 bytes"
        )
    return key


def encryption_key_id() -> str:
    return hashlib.sha256(_load_key()).hexdigest()[:16]


def encrypt_json(payload: dict, *, aad: str) -> tuple[str, str]:
    key = _load_key()
    nonce = os.urandom(12)
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad.encode("utf-8"))
    envelope = "v1:" + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")
    return envelope, hashlib.sha256(key).hexdigest()[:16]


def decrypt_json(envelope: str, *, aad: str) -> dict:
    if not envelope.startswith("v1:"):
        raise ValueError("Unsupported encrypted record version")
    raw = base64.urlsafe_b64decode(envelope[3:].encode("ascii"))
    if len(raw) < 29:
        raise ValueError("Invalid encrypted record")
    plaintext = AESGCM(_load_key()).decrypt(raw[:12], raw[12:], aad.encode("utf-8"))
    value = json.loads(plaintext.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Sensitive record payload must be an object")
    return value

