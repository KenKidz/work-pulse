"""Unit tests for the uniform error shape (no I/O beyond an in-process app)."""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from app.shared.circuit_breaker import CircuitOpenError
from app.shared.errors import (
    DependencyUnavailableError,
    ForbiddenError,
    RateLimitedError,
    UnauthenticatedError,
    WorkPulseError,
    error_response,
    register_error_handlers,
)


class DemoBody(BaseModel):
    name: str


def make_app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/demo")
    async def demo(body: DemoBody) -> dict[str, str]:
        return {"name": body.name}

    @app.get("/boom-forbidden")
    async def boom_forbidden() -> None:
        raise ForbiddenError("Not allowed")

    @app.get("/boom-unauthenticated")
    async def boom_unauthenticated() -> None:
        raise UnauthenticatedError()

    @app.get("/boom-rate-limited")
    async def boom_rate_limited() -> None:
        raise RateLimitedError(retry_after=17)

    @app.get("/boom-dependency")
    async def boom_dependency() -> None:
        raise DependencyUnavailableError()

    @app.get("/boom-circuit-open")
    async def boom_circuit_open() -> None:
        raise CircuitOpenError("minio")

    return app


client = TestClient(make_app())


def test_malformed_json_body_returns_uniform_validation_error() -> None:
    response = client.post(
        "/demo", content="{not json", headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "validation_error"
    # Messages never echo field values.
    assert "not json" not in body["error"]["message"]


def test_missing_required_field_returns_uniform_validation_error() -> None:
    response = client.post("/demo", json={})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_forbidden_error_shape() -> None:
    response = client.get("/boom-forbidden")

    assert response.status_code == 403
    assert response.json() == {"error": {"code": "forbidden", "message": "Not allowed"}}


def test_unauthenticated_error_shape() -> None:
    response = client.get("/boom-unauthenticated")

    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "unauthenticated", "message": "Authentication required"}
    }


def test_rate_limited_error_shape_and_retry_after_header() -> None:
    response = client.get("/boom-rate-limited")

    assert response.status_code == 429
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "rate_limited"
    assert response.headers["Retry-After"] == "17"


def test_rate_limited_retry_after_is_at_least_one_second() -> None:
    assert RateLimitedError(retry_after=0).retry_after == 1
    assert RateLimitedError(retry_after=-5).retry_after == 1


def test_dependency_unavailable_error_shape() -> None:
    response = client.get("/boom-dependency")

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "dependency_unavailable",
            "message": "A required service is temporarily unavailable",
        }
    }
    assert "Retry-After" not in response.headers


def test_circuit_open_error_maps_to_dependency_unavailable() -> None:
    response = client.get("/boom-circuit-open")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dependency_unavailable"


def test_middleware_can_render_the_rate_limited_response() -> None:
    """User middleware runs outside `ExceptionMiddleware` (T7 seam)."""
    app = FastAPI()
    register_error_handlers(app)

    class LimiterMiddleware(BaseHTTPMiddleware):
        async def dispatch(
            self,
            request: Request,
            call_next: Callable[[Request], Awaitable[Response]],
        ) -> Response:
            try:
                raise RateLimitedError(retry_after=9)
            except WorkPulseError as exc:
                return error_response(exc)

    app.add_middleware(LimiterMiddleware)

    @app.get("/demo-guarded")
    async def demo_guarded() -> dict[str, str]:
        return {"ok": "unreachable"}

    response = TestClient(app).get("/demo-guarded")

    assert response.status_code == 429
    assert response.json() == {
        "error": {
            "code": "rate_limited",
            "message": "Too many requests; retry after the indicated delay",
        }
    }
    assert response.headers["Retry-After"] == "9"
