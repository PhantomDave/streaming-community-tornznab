from __future__ import annotations

import asyncio
from pathlib import Path

from app.config import Settings
from app.db import Database
from app.downloads import worker as worker_module
from app.downloads.manager import DownloadManager
from app.models import Release, now_utc


def _make_release(infohash: str, release_name: str) -> Release:
    return Release(
        infohash=infohash,
        sc_id=1,
        sc_type="movie",
        slug="example",
        title="Example",
        year=2024,
        season=None,
        episode=None,
        resolution=1080,
        audio="ITA",
        size_estimate=1000,
        release_name=release_name,
        source_url="https://example.test/playlist.m3u8",
        created_at=now_utc(),
    )


def _make_manager(tmp_path: Path) -> tuple[DownloadManager, Database]:
    settings = Settings(
        download_path=str(tmp_path / "downloads"),
        db_path=str(tmp_path / "db" / "sctorznab.db"),
    )
    db = Database(settings.db_path)
    return DownloadManager(settings, db), db


async def _wait_until(predicate, timeout: float = 5.0) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition was not met in time")


def test_pause_terminates_running_subprocess_and_preserves_paused_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(worker_module, "build_ytdlp_command", lambda settings, release, output_path: ["sleep", "30"])

    manager, db = _make_manager(tmp_path)
    infohash = "abc123"
    db.upsert_release(_make_release(infohash, "Example.Release"))

    async def run() -> None:
        job = await manager.create_or_enqueue(infohash=infohash, category="radarr")
        run_task = asyncio.create_task(manager._run_job(job.id))
        try:
            await _wait_until(lambda: job.id in manager._processes)
            process = manager._processes[job.id]

            await manager.pause_hashes([infohash])

            await asyncio.wait_for(run_task, timeout=5)

            assert process.returncode is not None
            assert job.id not in manager._processes

            final = db.get_job(job.id)
            assert final is not None
            assert final.state == "paused"
        finally:
            if not run_task.done():
                run_task.cancel()

    asyncio.run(run())


def test_delete_hashes_terminates_process_and_removes_db_row(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(worker_module, "build_ytdlp_command", lambda settings, release, output_path: ["sleep", "30"])

    manager, db = _make_manager(tmp_path)
    infohash = "def456"
    db.upsert_release(_make_release(infohash, "Another.Release"))

    async def run() -> None:
        job = await manager.create_or_enqueue(infohash=infohash, category="radarr")
        run_task = asyncio.create_task(manager._run_job(job.id))
        try:
            await _wait_until(lambda: job.id in manager._processes)
            process = manager._processes[job.id]

            await manager.delete_hashes([infohash])

            await asyncio.wait_for(run_task, timeout=5)

            assert process.returncode is not None
            assert db.get_job(job.id) is None
        finally:
            if not run_task.done():
                run_task.cancel()

    asyncio.run(run())


def test_delete_hashes_with_delete_files_removes_download_directory(tmp_path) -> None:
    manager, db = _make_manager(tmp_path)
    infohash = "ghi789"
    release = _make_release(infohash, "Removable.Release")
    db.upsert_release(release)

    async def run() -> Path:
        return await manager.create_or_enqueue(infohash=infohash, category="radarr")

    job = asyncio.run(run())
    job_dir = Path(job.content_path).parent
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / f"{release.release_name}.mkv").write_bytes(b"fake video data")
    assert job_dir.exists()

    asyncio.run(manager.delete_hashes([infohash], delete_files=True))

    assert not job_dir.exists()
    assert db.get_job(job.id) is None


def test_delete_hashes_without_delete_files_keeps_download_directory(tmp_path) -> None:
    manager, db = _make_manager(tmp_path)
    infohash = "jkl012"
    release = _make_release(infohash, "Kept.Release")
    db.upsert_release(release)

    async def run() -> Path:
        return await manager.create_or_enqueue(infohash=infohash, category="radarr")

    job = asyncio.run(run())
    job_dir = Path(job.content_path).parent
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / f"{release.release_name}.mkv").write_bytes(b"fake video data")

    asyncio.run(manager.delete_hashes([infohash], delete_files=False))

    assert job_dir.exists()
    assert db.get_job(job.id) is None


def test_stalled_process_with_no_output_is_killed_and_marked_error(tmp_path, monkeypatch) -> None:
    # A yt-dlp/ffmpeg process that hangs mid-download (network stall, dead
    # peer, etc.) without exiting or printing anything must not leave the
    # job stuck in "downloading" forever — it should be killed and surfaced
    # as an error so the retry loop in DownloadManager._run_job can act on it.
    monkeypatch.setattr(worker_module, "build_ytdlp_command", lambda settings, release, output_path: ["sleep", "30"])

    settings = Settings(
        download_path=str(tmp_path / "downloads"),
        db_path=str(tmp_path / "db" / "sctorznab.db"),
        max_retries=0,
        download_stall_timeout=0.2,
    )
    db = Database(settings.db_path)
    manager = DownloadManager(settings, db)
    infohash = "stall001"
    db.upsert_release(_make_release(infohash, "Stalled.Release"))

    async def run() -> str:
        job = await manager.create_or_enqueue(infohash=infohash, category="radarr")
        await asyncio.wait_for(manager._run_job(job.id), timeout=5)
        return job.id

    job_id = asyncio.run(run())

    final = db.get_job(job_id)
    assert final is not None
    assert final.state == "error"
    assert "stalled" in (final.error or "").lower()


def test_start_recovers_jobs_stuck_from_previous_run(tmp_path, monkeypatch) -> None:
    # If the app restarts (crash/update) while a job was "downloading", the
    # in-memory queue and process registry both start empty, so without
    # recovery that job would sit stuck in its old state forever with no
    # process actually running. start() must re-queue it.
    monkeypatch.setattr(worker_module, "build_ytdlp_command", lambda settings, release, output_path: ["true"])

    manager, db = _make_manager(tmp_path)
    infohash = "stuck001"
    release = _make_release(infohash, "Stuck.Release")
    db.upsert_release(release)
    job = db.create_job(
        "job-stuck",
        infohash,
        "radarr",
        str(tmp_path / "downloads" / "radarr"),
        str(tmp_path / "downloads" / "radarr" / release.release_name / f"{release.release_name}.mkv"),
    )
    db.update_job_state(job.id, state="downloading", progress=0.42)

    async def run() -> None:
        await manager.start()
        try:
            await _wait_until(lambda: db.get_job(job.id).state in {"completed", "error"})
        finally:
            await manager.stop()

    asyncio.run(run())

    final = db.get_job(job.id)
    assert final is not None
    assert final.state == "completed"
