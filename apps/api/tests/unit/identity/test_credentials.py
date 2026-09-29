"""Unit tests for the credential helpers — no DB, argon2 round-trips only."""

import re

from app.domains.identity.credentials import (
    hash_password,
    new_session_token,
    session_token_hash,
    verify_password,
)


async def test_hash_verify_roundtrip() -> None:
    password = "correct horse battery"

    digest = await hash_password(password)

    assert digest != password
    assert digest.startswith("$argon2id$")  # pinned algorithm (T1)
    assert await verify_password(digest, password) is True
    assert await verify_password(digest, "wrong horse battery") is False


async def test_hash_is_salted_so_hashes_differ() -> None:
    first = await hash_password("same-password-here")
    second = await hash_password("same-password-here")

    assert first != second
    assert await verify_password(first, "same-password-here") is True
    assert await verify_password(second, "same-password-here") is True


async def test_verify_rejects_malformed_hash_without_raising() -> None:
    assert await verify_password("not-an-argon2-hash", "whatever") is False
    assert await verify_password("", "whatever") is False


def test_new_session_token_is_urlsafe_and_unique() -> None:
    tokens = {new_session_token() for _ in range(32)}

    assert len(tokens) == 32
    assert all(len(token) >= 43 for token in tokens)  # 32 random bytes
    assert all(re.fullmatch(r"[A-Za-z0-9_-]+", token) for token in tokens)


def test_session_token_hash_is_stable_sha256_hexdigest() -> None:
    token = "workpulse-session-token"

    digest = session_token_hash(token)

    assert digest == "42de5ab7a40f6f9da4e156f4d33b2df2d0299d7f56034696200cbc3f92d3dd58"
    assert session_token_hash(token) == digest  # stable lookup key
    assert session_token_hash("another-token") != digest
