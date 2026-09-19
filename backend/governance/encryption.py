"""Symmetric envelope encryption (AES-256-GCM) for sensitive results in the operation ledger."""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Final

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

DEFAULT_ENCRYPTION_SECRET: Final[str] = "agentdesk-default-aes-256-gcm-secret-key-32b"
NONCE_LENGTH_BYTES: Final[int] = 12


class ResultEncryptor:
    """Encrypts and decrypts sensitive ledger payloads using AES-256-GCM."""

    def __init__(self, key: bytes | str | None = None) -> None:
        if key is None:
            raw_key = os.environ.get("OPERATION_ENCRYPTION_KEY", DEFAULT_ENCRYPTION_SECRET)
            self._key = hashlib.sha256(raw_key.encode("utf-8")).digest()
        elif isinstance(key, str):
            self._key = hashlib.sha256(key.encode("utf-8")).digest()
        else:
            if len(key) != 32:
                self._key = hashlib.sha256(key).digest()
            else:
                self._key = key

        self._aesgcm = AESGCM(self._key)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt plaintext string and return base64-encoded nonce + ciphertext."""
        nonce = os.urandom(NONCE_LENGTH_BYTES)
        data = plaintext.encode("utf-8")
        ciphertext = self._aesgcm.encrypt(nonce, data, None)
        return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, token: str) -> str:
        """Decrypt base64-encoded nonce + ciphertext token to plaintext string."""
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        if len(raw) < NONCE_LENGTH_BYTES:
            raise ValueError("invalid ciphertext token: too short")
        nonce = raw[:NONCE_LENGTH_BYTES]
        ciphertext = raw[NONCE_LENGTH_BYTES:]
        data = self._aesgcm.decrypt(nonce, ciphertext, None)
        return data.decode("utf-8")
