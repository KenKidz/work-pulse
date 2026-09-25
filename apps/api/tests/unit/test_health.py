from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_healthz_returns_ok() -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_unknown_route_returns_404() -> None:
    response = client.get("/definitely-not-a-route")

    assert response.status_code == 404
