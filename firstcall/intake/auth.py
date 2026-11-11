"""Per-source intake auth: basic auth, a secret path token, or an HMAC signature.

Tokens are 128-bit random values stored as SHA-256 hashes; a fast hash is enough because
the token is random, not a password. Basic-auth passwords are stored as argon2id hashes.
HMAC secrets are read from the environment variable the source names.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError

from firstcall.db import Source

SIGNATURE_HEADER = "Firstcall-Signature"
TIMESTAMP_HEADER = "Firstcall-Timestamp"
MAX_SKEW_SECONDS = 300

_hasher = PasswordHasher()


def new_token() -> str:
    return secrets.token_urlsafe(16)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def check_token(source: Source, token: str | None) -> bool:
    if source.auth_method != "token" or not source.token_hash or not token:
        return False
    return hmac.compare_digest(source.token_hash, hash_token(token))


def check_basic(source: Source, authorization: str | None) -> bool:
    if source.auth_method != "basic" or not source.basic_password_hash or not authorization:
        return False
    scheme, _, encoded = authorization.partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        user, _, password = base64.b64decode(encoded, validate=True).decode().partition(":")
    except (ValueError, UnicodeDecodeError):
        return False
    if not hmac.compare_digest(user, source.basic_user or ""):
        return False
    try:
        return _hasher.verify(source.basic_password_hash, password)
    except (Argon2Error, InvalidHashError):
        return False


def sign(secret: str, timestamp: int, body: bytes) -> str:
    message = str(timestamp).encode() + b"." + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def check_hmac(
    source: Source, timestamp: str | None, signature: str | None, body: bytes, now: datetime
) -> bool:
    if source.auth_method != "hmac" or not source.hmac_secret_env:
        return False
    secret = os.environ.get(source.hmac_secret_env, "")
    if not secret or not timestamp or not signature:
        return False
    try:
        sent = int(timestamp)
    except ValueError:
        return False
    if abs(now.timestamp() - sent) > MAX_SKEW_SECONDS:
        return False
    expected = sign(secret, sent, body)
    return hmac.compare_digest(expected, signature.removeprefix("sha256="))
