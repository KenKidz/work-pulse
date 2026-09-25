"""Unit tests for the dev-identity header parsing (ADR 0004, pinned contract)."""

import pytest

from app.shared.errors import ForbiddenError, InvalidIdentityError
from app.shared.identity import Role, parse_identity

TENANT = "00000000-0000-4000-8000-000000000001"
ACTOR = "00000000-0000-4000-8000-000000000002"


def test_valid_headers_parse() -> None:
    identity = parse_identity(TENANT, ACTOR, "hr")

    assert str(identity.tenant_id) == TENANT
    assert str(identity.actor_id) == ACTOR
    assert identity.role is Role.HR


def test_role_is_case_insensitive() -> None:
    assert parse_identity(TENANT, ACTOR, "HR").role is Role.HR
    assert parse_identity(TENANT, ACTOR, "Super_Admin").role is Role.SUPER_ADMIN


def test_all_six_roles_accepted() -> None:
    for role in Role:
        assert parse_identity(TENANT, ACTOR, role.value).role is role


@pytest.mark.parametrize(
    ("tenant", "actor", "role"),
    [
        (None, ACTOR, "hr"),
        (TENANT, None, "hr"),
        (TENANT, ACTOR, None),
        ("", ACTOR, "hr"),
        ("not-a-uuid", ACTOR, "hr"),
        (TENANT, "not-a-uuid", "hr"),
        (TENANT, ACTOR, ""),
    ],
)
def test_missing_or_malformed_headers_raise_invalid_identity(
    tenant: str | None, actor: str | None, role: str | None
) -> None:
    with pytest.raises(InvalidIdentityError) as exc:
        parse_identity(tenant, actor, role)

    assert exc.value.status_code == 400
    assert exc.value.code == "invalid_identity"


def test_unknown_role_fails_closed_with_forbidden() -> None:
    with pytest.raises(ForbiddenError) as exc:
        parse_identity(TENANT, ACTOR, "ceo")

    assert exc.value.status_code == 403
    assert exc.value.code == "forbidden"
