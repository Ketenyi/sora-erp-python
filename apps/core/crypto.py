"""Encryption helpers for credentials stored in the database.

Keys are derived from ``SORA_SECRET_ENCRYPTION_KEY`` when supplied, otherwise
from Django's ``SECRET_KEY``. Values written through :func:`encrypt_secret`
carry an ``enc:v1:`` prefix so legacy plaintext rows keep working and can be
migrated gradually by re-saving them.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

PREFIX = "enc:v1:"


def _fernet() -> Fernet:
    raw = getattr(settings, "SORA_SECRET_ENCRYPTION_KEY", "") or settings.SECRET_KEY
    digest = hashlib.sha256(f"sora-secret-store::{raw}".encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    value = value or ""
    if not value or value.startswith(PREFIX):
        return value
    return PREFIX + _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    value = value or ""
    if not value.startswith(PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def is_encrypted(value: str) -> bool:
    return bool(value) and value.startswith(PREFIX)
