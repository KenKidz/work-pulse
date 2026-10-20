"""Redis presence-bus integration tests (`redis-test`, :6380).

The bus is the cross-process fan-out for the realtime board: one pub/sub
channel per tenant. Real Redis, no fakes — the adapter contract under test is
the JSON round-trip, channel isolation, and best-effort publishing (a
check-in must never fail because the bus is down).
"""

import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime
from uuid import UUID

from redis.asyncio import Redis

from app.infrastructure.redis.client import get_redis
from app.infrastructure.redis.presence import RedisPresencePublisher, tenant_channel
from app.infrastructure.redis.streams import RedisPresenceStream
from app.shared.presence import PresenceFact, PresenceReason, PresenceStatus

TENANT_A = UUID("00000000-0000-4000-8000-00000000000a")
TENANT_B = UUID("00000000-0000-4000-8000-00000000000b")
EMPLOYEE = UUID("00000000-0000-4000-8000-0000000000e1")
ATTENDANCE = UUID("00000000-0000-4000-8000-0000000000a1")


def _fact(
    tenant: UUID = TENANT_A,
    *,
    status: PresenceStatus = "present",
    reason: PresenceReason = "checked_in",
) -> PresenceFact:
    return PresenceFact(
        tenant_id=tenant,
        workday=date(2026, 10, 1),
        employee_id=EMPLOYEE,
        attendance_id=ATTENDANCE,
        status=status,
        late=False,
        minutes_late=None,
        since=datetime(2026, 10, 1, 8, 0, tzinfo=UTC),
        reason=reason,
        occurred_at=datetime(2026, 10, 1, 8, 0, tzinfo=UTC),
    )


async def _wait_subscribed(channel: str) -> None:
    """Wait until the SUBSCRIBE command has landed on the server.

    An async generator's body (and so `pubsub.subscribe`) only runs at the
    first `__anext__()`; polling `PUBSUB CHANNELS` removes the race between
    "consumer started" and "message published" without a sleep guess.
    """
    for _ in range(200):
        if channel.encode() in await get_redis().pubsub_channels():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"subscription to {channel} never registered")


async def _shutdown(
    stream: AsyncGenerator[PresenceFact, None], pending: asyncio.Future[PresenceFact]
) -> None:
    """Cancel a pending read (if any), then close the subscription generator."""
    if not pending.done():
        pending.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await pending
    await stream.aclose()


async def test_publish_round_trips_to_the_tenant_subscriber() -> None:
    publisher = RedisPresencePublisher(get_redis())
    stream = RedisPresenceStream(get_redis())
    fact = _fact(status="remote", reason="checked_in")

    subscription = stream.subscribe(TENANT_A)
    received_task = asyncio.ensure_future(subscription.__anext__())
    try:
        await _wait_subscribed(tenant_channel(TENANT_A))
        await publisher.publish(fact)
        received = await asyncio.wait_for(received_task, timeout=5)
    finally:
        await _shutdown(subscription, received_task)

    assert received == fact  # JSON round-trip preserves every field


async def test_tenants_never_cross_channels() -> None:
    publisher = RedisPresencePublisher(get_redis())
    stream = RedisPresenceStream(get_redis())

    subscription_a = stream.subscribe(TENANT_A)
    subscription_b = stream.subscribe(TENANT_B)
    received_a = asyncio.ensure_future(subscription_a.__anext__())
    received_b = asyncio.ensure_future(subscription_b.__anext__())
    try:
        await _wait_subscribed(tenant_channel(TENANT_A))
        await _wait_subscribed(tenant_channel(TENANT_B))

        fact = _fact(TENANT_A, reason="checked_out", status="checked_out")
        await publisher.publish(fact)

        assert (await asyncio.wait_for(received_a, timeout=5)).tenant_id == TENANT_A
        done, _ = await asyncio.wait({received_b}, timeout=0.3)
        assert received_b not in done  # tenant B's channel stayed silent
    finally:
        await _shutdown(subscription_a, received_a)
        await _shutdown(subscription_b, received_b)


async def test_publish_to_dead_redis_is_swallowed() -> None:
    dead = Redis.from_url(
        "redis://localhost:6399/0",
        socket_connect_timeout=0.25,
        socket_timeout=0.25,
        retry_on_timeout=False,
    )
    try:
        # Best-effort by contract: this must complete without raising.
        await RedisPresencePublisher(dead).publish(_fact())
    finally:
        with contextlib.suppress(Exception):
            await dead.aclose()


async def test_unknown_presence_fact_is_skipped_and_the_subscription_continues() -> (
    None
):
    """A newer worker's reason must not tear down an older reader's socket."""
    publisher = RedisPresencePublisher(get_redis())
    stream = RedisPresenceStream(get_redis())
    subscription = stream.subscribe(TENANT_A)
    received_task = asyncio.ensure_future(subscription.__anext__())
    try:
        await _wait_subscribed(tenant_channel(TENANT_A))

        # A fact shape/reason this reader does not know yet.
        future = _fact(status="on_leave", reason="leave_approved").model_dump(
            mode="json"
        )
        future["reason"] = "future_reason"
        await get_redis().publish(tenant_channel(TENANT_A), json.dumps(future))

        valid = _fact(status="remote", reason="checked_in")
        await publisher.publish(valid)
        received = await asyncio.wait_for(received_task, timeout=5)
    finally:
        await _shutdown(subscription, received_task)

    assert received == valid
