import asyncio

from app.config import Settings
from app.db import Database
from app.downloads import manager as manager_module
from app.downloads.manager import DownloadManager
from app.models import Release, now_utc


def _settings(tmp_path) -> Settings:
    return Settings(
        DB_PATH=str(tmp_path / "test.db"),
        DOWNLOAD_PATH=str(tmp_path / "downloads"),
        MAX_RETRIES=0,
        MAX_CONCURRENT_DOWNLOADS=1,
    )


def _release(infohash: str = "abc123") -> Release:
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
        size_estimate=1024,
        release_name="Dune.2021.1080p.WEB-DL.H264.ITA-SC",
        source_url="https://example.test/stream.m3u8",
        created_at=now_utc(),
    )


def test_unhandled_error_in_run_job_marks_job_as_error(tmp_path, monkeypatch) -> None:
    # A job that blows up with an exception the worker loop didn't
    # anticipate (e.g. a DB hiccup, an unexpected subprocess failure) must
    # not be left stuck in "resolving"/"downloading" forever with no way to
    # retry it — it should surface as a failed job like any other error path.
    settings = _settings(tmp_path)
    db = Database(settings.db_path)
    db.upsert_release(_release())

    async def _boom(*args, **kwargs):
        raise RuntimeError("subprocess exploded")

    monkeypatch.setattr(manager_module, "run_download_job", _boom)

    async def run() -> None:
        dm = DownloadManager(settings, db)
        await dm.start()
        try:
            job = await dm.create_or_enqueue(infohash="abc123", category="radarr")
            await dm._queue.join()
            return job.id
        finally:
            await dm.stop()

    job_id = asyncio.run(run())

    refreshed = db.get_job(job_id)
    assert refreshed is not None
    assert refreshed.state == "error"
    assert "subprocess exploded" in (refreshed.error or "")
