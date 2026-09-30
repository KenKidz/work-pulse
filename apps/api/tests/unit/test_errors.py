"""Unit tests for the uniform error shape (no I/O beyond an in-process app)."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.shared.errors import (
    ForbiddenError,
    UnauthenticatedError,
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
