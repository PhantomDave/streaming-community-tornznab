from fastapi.testclient import TestClient

from app.db import Database
from app.deps import get_db
from app.main import app
from app.models import Release, now_utc


def _sample_release(infohash: str = "hash1") -> Release:
    return Release(
        infohash=infohash,
        sc_id=1,
        sc_type="movie",
        slug="dune",
        title="Dune",
        year=2021,
        season=None,
        episode=None,
        resolution=1080,
        audio="ITA",
        size_estimate=123456,
        release_name="Dune.2021.1080p.WEB-DL.H264.ITA-SC",
        source_url="https://example.test/master.m3u8",
        created_at=now_utc(),
    )


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_qbit_app_version() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v2/app/version")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        assert response.text == "v4.6.0"


def test_qbit_webapi_version() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v2/app/webapiVersion")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        assert response.text == "2.9.2"


def test_torznab_caps() -> None:
    with TestClient(app) as client:
        response = client.get("/torznab/api?t=caps")
        assert response.status_code == 200
        assert "application/xml" in response.headers["content-type"]


def test_qbit_auth_login_success_sets_cookie() -> None:
    with TestClient(app) as client:
        response = client.post("/api/v2/auth/login", data={"username": "admin", "password": "adminadmin"})
    assert response.status_code == 200
    assert response.text == "Ok."
    assert "SID" in response.cookies


def test_qbit_auth_login_failure_returns_403() -> None:
    with TestClient(app) as client:
        response = client.post("/api/v2/auth/login", data={"username": "admin", "password": "wrong"})
    assert response.status_code == 403
    assert response.text == "Fails."


def test_qbit_read_endpoints_and_category_filtering(tmp_path) -> None:
    db = Database(str(tmp_path / "qbit.db"))
    first_release = _sample_release("hash1")
    second_release = _sample_release("hash2")
    db.upsert_release(first_release)
    db.upsert_release(second_release)
    first_job = db.create_job("job-1", first_release.infohash, "radarr", "/downloads/radarr", "/downloads/radarr/file1.mkv")
    db.create_job("job-2", second_release.infohash, "sonarr", "/downloads/sonarr", "/downloads/sonarr/file2.mkv")
    db.update_job_state(first_job.id, state="completed", progress=1.0, bytes_done=123456, bytes_total=123456)

    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            info_response = client.get("/api/v2/torrents/info", params={"category": "radarr"})
            properties_response = client.get("/api/v2/torrents/properties", params={"hash": first_release.infohash})
            files_response = client.get("/api/v2/torrents/files", params={"hash": first_release.infohash})
            missing_properties_response = client.get("/api/v2/torrents/properties", params={"hash": "missing"})
            missing_files_response = client.get("/api/v2/torrents/files", params={"hash": "missing"})
            transfer_response = client.get("/api/v2/transfer/info")
    finally:
        app.dependency_overrides.clear()

    assert info_response.status_code == 200
    info_payload = info_response.json()
    assert len(info_payload) == 1
    assert info_payload[0]["hash"] == "hash1"
    assert info_payload[0]["state"] == "pausedUP"
    assert info_payload[0]["category"] == "radarr"

    assert properties_response.status_code == 200
    assert properties_response.json()["total_size"] == 123456

    assert files_response.status_code == 200
    assert files_response.json()[0]["name"] == "Dune.2021.1080p.WEB-DL.H264.ITA-SC.mkv"

    assert missing_properties_response.status_code == 200
    assert missing_properties_response.json() == {}
    assert missing_files_response.status_code == 200
    assert missing_files_response.json() == []

    assert transfer_response.status_code == 200
    assert transfer_response.json() == {
        "dl_info_speed": 0,
        "up_info_speed": 0,
        "dl_info_data": 0,
        "up_info_data": 0,
    }
