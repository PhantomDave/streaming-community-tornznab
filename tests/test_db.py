import sqlite3

from app.db import Database
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


def test_release_and_job_lifecycle(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    release = _sample_release()
    db.upsert_release(release)
    stored = db.get_release(release.infohash)
    assert stored is not None
    assert stored.release_name == release.release_name

    job = db.create_job("job-1", release.infohash, "radarr", "/downloads/radarr", "/downloads/radarr/file.mkv")
    assert job.state == "queued"
    db.update_job_state(job.id, state="downloading", progress=0.5, bytes_done=50, bytes_total=100)
    updated = db.get_job(job.id)
    assert updated is not None
    assert updated.state == "downloading"
    assert updated.progress == 0.5

    db.set_job_category(release.infohash, "sonarr")
    by_hash = db.get_job_by_infohash(release.infohash)
    assert by_hash is not None
    assert by_hash.category == "sonarr"

    db.delete_job([release.infohash])
    assert db.get_job(job.id) is None


def test_cache_expiration(tmp_path) -> None:
    db = Database(str(tmp_path / "cache.db"))
    db.cache_set("title_cache", "a", {"k": "v"}, ttl_seconds=60)
    assert db.cache_get("title_cache", "a") == {"k": "v"}

    db.cache_set("playlist_cache", "expired", {"x": 1}, ttl_seconds=-1)
    assert db.cache_get("playlist_cache", "expired") is None


def test_release_upsert_updates_selected_fields(tmp_path) -> None:
    db = Database(str(tmp_path / "upsert.db"))
    original = _sample_release("hash2")
    db.upsert_release(original)

    updated = _sample_release("hash2")
    updated.release_name = "Dune.2021.2160p.WEB-DL.H265.ITA-SC"
    updated.size_estimate = 654321
    updated.source_url = "https://example.test/updated.m3u8"
    db.upsert_release(updated)

    stored = db.get_release("hash2")
    assert stored is not None
    assert stored.release_name == "Dune.2021.2160p.WEB-DL.H265.ITA-SC"
    assert stored.size_estimate == 654321
    assert stored.source_url == "https://example.test/updated.m3u8"
    assert stored.title == "Dune"


def test_opening_pre_migration_db_adds_new_job_columns_without_losing_rows(tmp_path) -> None:
    # Simulates a jobs table created before retry_count/error_kind/
    # last_progress_at existed (no migration framework — Database must patch
    # existing sqlite files in place via ALTER TABLE, not just CREATE TABLE
    # IF NOT EXISTS, which is a no-op once the table already exists).
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
            infohash TEXT NOT NULL,
            category TEXT NOT NULL,
            state TEXT NOT NULL,
            progress REAL NOT NULL,
            bytes_done INTEGER NOT NULL,
            bytes_total INTEGER NOT NULL,
            save_path TEXT NOT NULL,
            content_path TEXT NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO jobs VALUES ('job-legacy', 'hash-legacy', 'radarr', 'completed', 1.0, 100, 100, "
        "'/downloads/radarr', '/downloads/radarr/file.mkv', NULL, '2024-01-01T00:00:00+00:00', "
        "'2024-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    db = Database(str(db_path))

    job = db.get_job("job-legacy")
    assert job is not None
    assert job.retry_count == 0
    assert job.error_kind is None
    assert job.last_progress_at is None


def test_categories_are_sorted_and_unique(tmp_path) -> None:
    db = Database(str(tmp_path / "categories.db"))
    db.ensure_category("sonarr")
    db.ensure_category("radarr")
    db.ensure_category("sonarr")
    assert db.list_categories() == ["radarr", "sonarr"]
