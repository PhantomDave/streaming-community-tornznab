from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_qbit_app_version() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v2/app/version")
        assert response.status_code == 200
        assert response.json() == "v4.6.0"


def test_torznab_caps() -> None:
    with TestClient(app) as client:
        response = client.get("/torznab/api?t=caps")
        assert response.status_code == 200
        assert "application/xml" in response.headers["content-type"]
