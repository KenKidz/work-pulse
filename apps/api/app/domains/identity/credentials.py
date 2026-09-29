"""Credential helpers — password hashing and session tokens (pinned in T1).

Passwords: argon2id via argon2-cffi `PasswordHasher` defaults (t=3, m=64 MiB,
p=4). Hashing/verification is sync CPU work and runs off the event loop
(`anyio.to_thread`).

Session tokens: 32 random bytes (`secrets.token_urlsafe`) — the cookie carries
the raw token; `sessions.token_hash` stores its SHA-256 hexdigest as a lookup
key only (the token is high-entropy, the hash is not a secret).
"""

import hashlib
import secrets

import anyio
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

# Pinned cookie contract (`.ai/features/identity-access.md` → Pinned details):
# HttpOnly, SameSite=Lax, Path=/; `Secure` is settings-driven
# (`Settings.session_cookie_secure`).
SESSION_COOKIE_NAME = "wp_session"

_hasher = PasswordHasher()


async def hash_password(password: str) -> str:
    """Hash a plaintext password with argon2id (off the event loop)."""
    return await anyio.to_thread.run_sync(_hasher.hash, password)


async def verify_password(password_hash: str, password: str) -> bool:
    """Verify a password against its argon2 hash (off the event loop).

    A mismatch or a malformed/unknown hash format is simply `False` — never an
    exception and never detail about why (no enumeration signal).
    """

    def _verify() -> bool:
        try:
            return _hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            # Mismatch or malformed/unknown hash format — both are a plain
            # "no", never an exception and never detail about why.
            return False

    return await anyio.to_thread.run_sync(_verify)


def new_session_token() -> str:
    """A fresh 256-bit session token (url-safe, cookie-friendly)."""
    return secrets.token_urlsafe(32)


def session_token_hash(token: str) -> str:
    """The SHA-256 hexdigest stored in `sessions.token_hash` (lookup key only)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
