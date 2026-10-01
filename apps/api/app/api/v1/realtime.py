"""Realtime Command Center routes: the REST snapshot and the live WS stream.

One resource (`/realtime/board`), two transports, one payload. Both resolve
identity from the `wp_session` cookie through `app/dependencies.py` (ADR
0010), exactly like every other route; thin by rule — identity → policy →
service, with `now` read once at this edge (`_now()`).

Handshake denials are **close-before-accept with zero application frames**
(pinned): the WebSocket API has no channel for an upgrade status, so a
missing/invalid session (`identity is None`) and an insufficient role both
close before `accept` — uvicorn answers that with one upgrade status, 403.
The REST route keeps the uniform 401/403 error shape.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from app.dependencies import (
    IdentityDep,
    RealtimeServiceDep,
    RealtimeStreamServiceDep,
    SocketIdentityDep,
)
from app.domains.realtime.policies import ensure_can_read_presence
from app.domains.realtime.schemas import RealtimeBoardSnapshot
from app.shared.errors import ForbiddenError

router = APIRouter()


def _now() -> datetime:
    """The single place wall-clock time is read (time is a parameter below)."""
    return datetime.now(UTC)


@router.get(
    "/realtime/board",
    response_model=RealtimeBoardSnapshot,
    operation_id="getRealtimeBoard",
)
async def get_realtime_board(
    identity: IdentityDep,
    service: RealtimeServiceDep,
) -> RealtimeBoardSnapshot:
    """The current workday's board — initial render and degraded mode.

    Serves from PostgreSQL only (the bus is never touched): a Redis outage
    degrades latency on the live stream, never this snapshot's correctness.
    """
    ensure_can_read_presence(identity)
    return await service.snapshot(identity, _now())


async def _forward_frames(websocket: WebSocket, frames: AsyncIterator[str]) -> None:
    """Send frames in order until the stream ends or the socket drops.

    A client disconnect surfaces as `WebSocketDisconnect` (Starlette maps the
    transport's `OSError`), which the caller swallows; any other failure —
    e.g. the Redis subscription dying — propagates to close the socket so the
    client resyncs or falls back to the REST snapshot.
    """
    async for frame in frames:
        await websocket.send_text(frame)


async def _receive_until_disconnect(websocket: WebSocket) -> None:
    """Consume client messages; v1 has no client → server frames but a
    concurrent receive is how a disconnect is detected promptly (uvicorn
    answers protocol ping/pong itself)."""
    while (await websocket.receive())["type"] != "websocket.disconnect":
        continue


@router.websocket("/realtime/board")
async def stream_realtime_board(
    websocket: WebSocket,
    identity: SocketIdentityDep,
    service: RealtimeStreamServiceDep,
) -> None:
    """The live stream: a `snapshot` frame first, then `presence.upsert`s."""
    if identity is None:
        await websocket.close()  # close-before-accept → single upgrade status
        return
    try:
        ensure_can_read_presence(identity)
    except ForbiddenError:
        await websocket.close()  # same denial, zero application frames
        return

    await websocket.accept()
    sender = asyncio.create_task(
        _forward_frames(websocket, service.stream(identity, _now()))
    )
    receiver = asyncio.create_task(_receive_until_disconnect(websocket))
    try:
        # Either the client goes away (receiver wins) or the stream/transport
        # fails (sender wins — its exception closes the connection).
        await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        sender.cancel()
        receiver.cancel()
        for task in (sender, receiver):
            with suppress(asyncio.CancelledError, WebSocketDisconnect):
                await task
