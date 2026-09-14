"""Argon2 password hashing.

Plaintext passwords never leave this module: callers pass a ``SecretStr`` and
only the encoded hash is stored.
"""

from __future__ import annotations

from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from pydantic import SecretStr

_password_hasher = PasswordHash.recommended()


def hash_password(password: SecretStr) -> str:
    return _password_hasher.hash(password.get_secret_value())


def verify_password(password: SecretStr, encoded: str) -> bool:
    try:
        return _password_hasher.verify(password.get_secret_value(), encoded)
    except UnknownHashError:
        return False
