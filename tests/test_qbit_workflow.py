from fastapi.testclient import TestClient

from app.db import Database
from app.deps import get_db, get_download_manager
from app.main import app
from app.magnet import build_magnet


class FakeDownloadManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def create_or_enqueue(self, *, infohash: str, category: str):
        self.calls.append(("add", infohash, category))
        return {"ok": True}

    async def pause_hashes(self, hashes: list[str]) -> None:
        self.calls.append(("pause", ",".join(hashes), ""))

    async def resume_hashes(self, hashes: list[str]) -> None:
        self.calls.append(("resume", ",".join(hashes), ""))

    async def delete_hashes(self, hashes: list[str], *, delete_files: bool = False) -> None:
        self.calls.append(("delete", ",".join(hashes), "true" if delete_files else ""))


def test_qbit_add_pause_resume_delete_workflow() -> None:
    fake_manager = FakeDownloadManager()
    app.dependency_overrides[get_download_manager] = lambda: fake_manager
    try:
        with TestClient(app) as client:
            magnet = build_magnet("abc123", "Example.Release")
            add_response = client.post("/api/v2/torrents/add", data={"urls": magnet, "category": "radarr"})
            pause_response = client.post("/api/v2/torrents/pause", data={"hashes": "abc123"})
            resume_response = client.post("/api/v2/torrents/resume", data={"hashes": "abc123"})
            delete_response = client.post("/api/v2/torrents/delete", data={"hashes": "abc123", "deleteFiles": "false"})

            assert add_response.status_code == 200
            assert add_response.text == "Ok."
            assert pause_response.status_code == 200
            assert resume_response.status_code == 200
            assert delete_response.status_code == 200
    finally:
        app.dependency_overrides.clear()

    assert fake_manager.calls == [
        ("add", "abc123", "radarr"),
        ("pause", "abc123", ""),
        ("resume", "abc123", ""),
        ("delete", "abc123", ""),
    ]


def test_qbit_delete_forwards_delete_files_flag() -> None:
    fake_manager = FakeDownloadManager()
    app.dependency_overrides[get_download_manager] = lambda: fake_manager
    try:
        with TestClient(app) as client:
            delete_response = client.post("/api/v2/torrents/delete", data={"hashes": "abc123", "deleteFiles": "true"})
    finally:
        app.dependency_overrides.clear()

    assert delete_response.status_code == 200
    assert fake_manager.calls == [("delete", "abc123", "true")]


def test_qbit_categories_endpoint_roundtrip() -> None:
    with TestClient(app) as client:
        create_response = client.post("/api/v2/torrents/createCategory", data={"category": "sonarr"})
        assert create_response.status_code == 200
        categories = client.get("/api/v2/torrents/categories")
        assert categories.status_code == 200
        payload = categories.json()
        assert "sonarr" in payload


def test_qbit_create_category_rejects_path_traversal() -> None:
    with TestClient(app) as client:
        response = client.post("/api/v2/torrents/createCategory", data={"category": "../evil"})
        categories = client.get("/api/v2/torrents/categories").json()

    assert response.status_code == 400
    assert response.text == "Fails."
    assert "../evil" not in categories


def test_qbit_set_category_rejects_path_traversal(tmp_path) -> None:
    db = Database(str(tmp_path / "qbit.db"))
    job = db.create_job(
        "job-trav",
        "travhash1",
        "radarr",
        str(tmp_path / "downloads" / "radarr"),
        str(tmp_path / "downloads" / "radarr" / "release.mkv"),
    )

    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v2/torrents/setCategory",
                data={"hashes": "travhash1", "category": "../../etc"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.text == "Fails."
    refreshed = db.get_job(job.id)
    assert refreshed is not None
    assert refreshed.category == "radarr"


def test_qbit_add_invalid_magnet_returns_400() -> None:
    fake_manager = FakeDownloadManager()
    app.dependency_overrides[get_download_manager] = lambda: fake_manager
    try:
        with TestClient(app) as client:
            response = client.post("/api/v2/torrents/add", data={"urls": "https://example.test/file.torrent", "category": "radarr"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.text == "Fails."
    assert fake_manager.calls == []


def test_qbit_add_unknown_release_returns_404() -> None:
    class MissingReleaseDownloadManager:
        async def create_or_enqueue(self, *, infohash: str, category: str):
            raise ValueError("Unknown release")

    app.dependency_overrides[get_download_manager] = lambda: MissingReleaseDownloadManager()
    try:
        with TestClient(app) as client:
            magnet = build_magnet("abc123", "Example.Release")
            response = client.post("/api/v2/torrents/add", data={"urls": magnet, "category": "radarr"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.text == "Fails."


def test_qbit_multi_hash_workflow_commands() -> None:
    fake_manager = FakeDownloadManager()
    app.dependency_overrides[get_download_manager] = lambda: fake_manager
    try:
        with TestClient(app) as client:
            pause_response = client.post("/api/v2/torrents/pause", data={"hashes": "abc123|def456"})
            resume_response = client.post("/api/v2/torrents/resume", data={"hashes": "abc123|def456"})
            delete_response = client.post("/api/v2/torrents/delete", data={"hashes": "abc123|def456", "deleteFiles": "false"})
    finally:
        app.dependency_overrides.clear()

    assert pause_response.status_code == 200
    assert resume_response.status_code == 200
    assert delete_response.status_code == 200
    assert fake_manager.calls == [
        ("pause", "abc123,def456", ""),
        ("resume", "abc123,def456", ""),
        ("delete", "abc123,def456", ""),
    ]
