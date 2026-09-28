"""AC11 — no name, email or phone value appears in any log line of these flows.

These flows log almost nothing, so this can pass trivially — it still fails the
moment someone logs a value (spec Risk 6). The request middleware's log lines
(`method`, `path`, `status_code`, correlation ids) are covered too.
"""

import json

import structlog

from tests.integration.conftest import (
    ClientFactory,
    create_org_refs,
    employee_payload,
)

PII_VALUES = ["Zorblax", "Qqweird", "pii-unique@example.test", "+84-555-0100"]


async def test_employee_flows_log_no_pii(client_factory: ClientFactory) -> None:
    # Force lazy loggers to resolve the capture config (cache_logger_on_first_use).
    structlog.configure(cache_logger_on_first_use=False)

    async with client_factory() as client:
        refs = await create_org_refs(client)
        with structlog.testing.capture_logs() as logs:
            created_response = await client.post(
                "/api/v1/employees",
                json=employee_payload(
                    refs,
                    first_name="Zorblax",
                    last_name="Qqweird",
                    email="pii-unique@example.test",
                    phone="+84-555-0100",
                ),
            )
            created = created_response.json()
            await client.patch(
                f"/api/v1/employees/{created['id']}",
                json={"first_name": "Renamed", "phone": "+84-555-0199"},
            )
            await client.post(
                f"/api/v1/employees/{created['id']}/transfers",
                json={"department_id": refs[0], "effective_from": "2026-06-01"},
            )
            await client.post(
                f"/api/v1/employees/{created['id']}/termination",
                json={"effective_date": "2026-09-01", "reason": "offboarding"},
            )

    # The capture must actually see the request log lines — otherwise the PII
    # assertions below would pass trivially (spec Risk 6).
    assert logs, "expected the request middleware to log each call"
    dumped = json.dumps(logs, default=str)
    for value in PII_VALUES:
        assert value not in dumped
    assert "Renamed" not in dumped
    assert "offboarding" not in dumped
