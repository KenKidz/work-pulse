"""Contract tests — realtime board frames vs `contracts/realtime/board.v1.json`.

The frame union at the contract root is a projection protocol, not the event
catalog: every frame `RealtimeService` produces validates against the
checked-in schema, the REST snapshot body validates against
`#/$defs/board-snapshot`, and no frame carries a PII-shaped key. `reason` is
pinned in the contract as the transition cause (not free text), so — unlike
the event tests — it is deliberately not treated as a PII marker here.
"""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator, ValidationError

from app.domains.attendance.service import AttendanceService
from app.domains.realtime.service import RealtimeService
from app.shared.presence import PresenceFact, PresenceReason, PresenceStatus
from tests.unit.attendance.conftest import (
    ACTOR,
    NOW,
    REPORT,
    TENANT_A,
    TODAY,
    identity,
    seed_employee,
    seed_record,
)
from tests.unit.attendance.fakes import (
    FakeAttendanceRepo,
    FakePresencePublisher,
    FakeShiftRepo,
)
from tests.unit.organizations.fakes import RecordingAuditWriter

REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMA_PATH = REPO_ROOT / "contracts" / "realtime" / "board.v1.json"

SNAPSHOT_FRAME_KEYS = {"type", "date", "counts", "presence", "activity"}
UPSERT_FRAME_KEYS = {"type", "date", "counts", "row", "occurred_at", "reason"}
ROW_KEYS = {"employee_id", "status", "late", "minutes_late", "since", "attendance_id"}
COUNT_KEYS = {"present", "remote", "late", "on_leave", "absent", "checked_out"}
ACTIVITY_KEYS = {"occurred_at", "employee_id", "action"}

# `reason`/`since`/`late` are pinned frame vocabulary, never PII markers.
_PII_KEY_MARKERS = ("name", "email", "phone", "salary", "note", "address")

ATTENDANCE = UUID("00000000-0000-4000-8000-0000000000a1")


def _fact(
    *,
    employee_id: UUID = REPORT,
    status: PresenceStatus = "present",
    reason: PresenceReason = "checked_in",
    since: datetime = NOW,
) -> PresenceFact:
    return PresenceFact(
        tenant_id=TENANT_A,
        workday=TODAY,
        employee_id=employee_id,
        attendance_id=ATTENDANCE,
        status=status,
        late=False,
        minutes_late=None,
        since=since,
        reason=reason,
        occurred_at=NOW,
    )


class _ScriptedStream:
    """Yields the listed facts, then ends (so frame production is finite)."""

    def __init__(self, facts: list[PresenceFact]) -> None:
        self._facts = facts

    async def subscribe(self, tenant_id: UUID) -> AsyncIterator[PresenceFact]:
        for fact in self._facts:
            yield fact


async def _produced_frames() -> list[dict[str, Any]]:
    """A real snapshot + two deltas, exactly as a connection would receive them."""
    repo = FakeAttendanceRepo()
    seed_employee(repo, employee_id=ACTOR)
    seed_employee(repo, employee_id=REPORT)
    await seed_record(repo, employee_id=ACTOR)
    stream = _ScriptedStream(
        [
            _fact(employee_id=REPORT),
            _fact(
                employee_id=REPORT,
                status="checked_out",
                reason="checked_out",
                since=datetime(2026, 9, 24, 18, 0, tzinfo=UTC),
            ),
        ]
    )
    attendance = AttendanceService(
        repo=repo,
        shifts=FakeShiftRepo(),
        audit=RecordingAuditWriter(),
        presence=FakePresencePublisher(),
    )
    service = RealtimeService(attendance=attendance, stream=stream)
    return [json.loads(frame) async for frame in service.stream(identity(), NOW)]


def _pii_keys(obj: Any) -> list[str]:
    """Return keys in the frame tree whose names look PII-shaped."""
    found: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if any(marker in key.lower() for marker in _PII_KEY_MARKERS):
                found.append(key)
            found.extend(_pii_keys(value))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_pii_keys(item))
    return found


def _rest_validator(schema: dict[str, Any]) -> Draft202012Validator:
    """The REST-body subschema, resolved against the contract's `$defs`."""
    return Draft202012Validator(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": "#/$defs/board-snapshot",
            "$defs": schema["$defs"],
        }
    )


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(SCHEMA_PATH.read_text())
    Draft202012Validator.check_schema(loaded)
    return loaded


@pytest.fixture(scope="module")
def validator(schema: dict[str, Any]) -> Draft202012Validator:
    return Draft202012Validator(schema)


async def test_every_produced_frame_validates(
    validator: Draft202012Validator,
) -> None:
    frames = await _produced_frames()

    assert [frame["type"] for frame in frames] == [
        "snapshot",
        "presence.upsert",
        "presence.upsert",
    ]
    for frame in frames:
        validator.validate(frame)


async def test_frames_carry_exactly_the_pinned_keys() -> None:
    for frame in await _produced_frames():
        assert set(frame["counts"]) == COUNT_KEYS
        if frame["type"] == "snapshot":
            assert set(frame) == SNAPSHOT_FRAME_KEYS
            for row in frame["presence"]:
                assert set(row) == ROW_KEYS
            for entry in frame["activity"]:
                assert set(entry) == ACTIVITY_KEYS
        else:
            assert set(frame) == UPSERT_FRAME_KEYS
            assert set(frame["row"]) == ROW_KEYS


async def test_rest_snapshot_body_validates_against_its_def(
    schema: dict[str, Any],
) -> None:
    frames = await _produced_frames()
    body = {key: value for key, value in frames[0].items() if key != "type"}

    _rest_validator(schema).validate(body)


async def test_frames_carry_no_pii_shaped_keys() -> None:
    for frame in await _produced_frames():
        pii = _pii_keys(frame)
        assert pii == [], f"PII-shaped key(s) in {frame['type']}: {pii}"


async def test_schema_rejects_unknown_pii_and_bad_discriminator(
    validator: Draft202012Validator,
) -> None:
    frames = await _produced_frames()
    upsert = frames[1]

    with pytest.raises(ValidationError):
        validator.validate({**frames[0], "employee_name": "Ada"})
    with pytest.raises(ValidationError):
        validator.validate({**frames[0], "type": "presence.updated"})
    with pytest.raises(ValidationError):
        validator.validate({**upsert, "row": {**upsert["row"], "note": "late bus"}})


def test_schema_is_a_union_discriminated_on_type(schema: dict[str, Any]) -> None:
    """The root stays the frame union (oneOf), not a single frame shape."""
    assert len(schema["oneOf"]) == 2
    snapshot, upsert = (
        schema["$defs"]["snapshot-frame"],
        schema["$defs"]["presence-upsert-frame"],
    )
    assert snapshot["properties"]["type"]["const"] == "snapshot"
    assert upsert["properties"]["type"]["const"] == "presence.upsert"
