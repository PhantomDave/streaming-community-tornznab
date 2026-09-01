from __future__ import annotations

import asyncio
from pathlib import Path
import shlex

from app.config import Settings
from app.db import Database
from app.downloads import worker as worker_module
from app.downloads.manager import DownloadManager, SpeedTracker
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


def test_stalled_process_with_repeating_non_advancing_output_is_killed(tmp_path, monkeypatch) -> None:
    # Regression test: a process that keeps emitting output (e.g. ffmpeg
    # repainting the same non-advancing "time=" line) must still be detected
    # as stalled. The old "no bytes for X seconds" detector reset its clock on
    # any byte read, so it would never fire here even though the download is
    # frozen — only a genuine increase in parsed progress may reset the clock.
    script = tmp_path / "repeat_progress.py"
    script.write_text(
        "import sys, time\n"
        "while True:\n"
        "    sys.stdout.write('[download]  45.0% of 100MiB\\n')\n"
        "    sys.stdout.flush()\n"
        "    time.sleep(0.02)\n"
    )
    monkeypatch.setattr(
        worker_module,
        "build_ytdlp_command",
        lambda settings, release, output_path: ["python3", str(script)],
    )

    settings = Settings(
        download_path=str(tmp_path / "downloads"),
        db_path=str(tmp_path / "db" / "sctorznab.db"),
        max_retries=0,
        download_stall_timeout=0.3,
        download_progress_poll_interval=0.05,
    )
    db = Database(settings.db_path)
    manager = DownloadManager(settings, db)
    infohash = "stall002"
    db.upsert_release(_make_release(infohash, "Frozen.Release"))

    async def run() -> str:
        job = await manager.create_or_enqueue(infohash=infohash, category="radarr")
        await asyncio.wait_for(manager._run_job(job.id), timeout=5)
        return job.id

    job_id = asyncio.run(run())

    final = db.get_job(job_id)
    assert final is not None
    assert final.state == "error"
    assert final.error_kind == "transient"
    assert "stalled" in (final.error or "").lower()


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


def test_watchdog_requeues_transient_error_job_after_backoff_elapses(tmp_path) -> None:
    manager, db = _make_manager(tmp_path)
    infohash = "transient001"
    release = _make_release(infohash, "Transient.Release")
    db.upsert_release(release)
    job = db.create_job(
        "job-transient",
        infohash,
        "radarr",
        str(tmp_path / "downloads" / "radarr"),
        str(tmp_path / "downloads" / "radarr" / release.release_name / f"{release.release_name}.mkv"),
    )
    db.record_job_failure(job.id, error="Download stalled: no progress for 180s", error_kind="transient")
    # Backdate updated_at so the (small, test-configured) backoff has already
    # elapsed without needing to actually sleep in the test.
    with db._lock, db._connect() as conn:
        conn.execute("UPDATE jobs SET updated_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (job.id,))

    asyncio.run(manager._retry_stalled_error_jobs())

    refreshed = db.get_job(job.id)
    assert refreshed is not None
    assert refreshed.state == "queued"
    assert refreshed.error_kind == ""
    assert manager._queue.get_nowait() == job.id


def test_watchdog_ignores_permanent_error_jobs(tmp_path) -> None:
    manager, db = _make_manager(tmp_path)
    infohash = "permanent001"
    release = _make_release(infohash, "Permanent.Release")
    db.upsert_release(release)
    job = db.create_job(
        "job-permanent",
        infohash,
        "radarr",
        str(tmp_path / "downloads" / "radarr"),
        str(tmp_path / "downloads" / "radarr" / release.release_name / f"{release.release_name}.mkv"),
    )
    db.record_job_failure(job.id, error="Missing source URL", error_kind="permanent")
    with db._lock, db._connect() as conn:
        conn.execute("UPDATE jobs SET updated_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (job.id,))

    asyncio.run(manager._retry_stalled_error_jobs())

    refreshed = db.get_job(job.id)
    assert refreshed is not None
    assert refreshed.state == "error"
    assert refreshed.error_kind == "permanent"
    assert manager._queue.empty()


def test_start_recovers_jobs_stuck_from_previous_run(tmp_path, monkeypatch) -> None:
    # If the app restarts (crash/update) while a job was "downloading", the
    # in-memory queue and process registry both start empty, so without
    # recovery that job would sit stuck in its old state forever with no
    # process actually running. start() must re-queue it.
    monkeypatch.setattr(
        worker_module,
        "build_ytdlp_command",
        lambda settings, release, output_path: ["sh", "-c", f"echo fake-download-output > {shlex.quote(str(output_path))}"],
    )

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


def test_speed_tracker_computes_rate_from_recent_samples(monkeypatch) -> None:
    tracker = SpeedTracker()
    clock = iter([100.0, 101.0, 102.0])
    monkeypatch.setattr("app.downloads.manager.time.monotonic", lambda: next(clock))

    tracker.record("job-1", 0)
    tracker.record("job-1", 1_000_000)
    tracker.record("job-1", 2_000_000)

    assert tracker.speed("job-1") == 1_000_000.0


def test_speed_tracker_returns_zero_without_enough_samples() -> None:
    tracker = SpeedTracker()
    assert tracker.speed("unknown-job") == 0.0

    tracker.record("job-1", 500)
    assert tracker.speed("job-1") == 0.0


def test_speed_tracker_reset_and_discard_clear_samples(monkeypatch) -> None:
    tracker = SpeedTracker()
    clock = iter([100.0, 101.0, 102.0, 103.0])
    monkeypatch.setattr("app.downloads.manager.time.monotonic", lambda: next(clock))

    tracker.record("job-1", 0)
    tracker.record("job-1", 1_000)
    assert tracker.speed("job-1") > 0.0

    tracker.reset("job-1")
    assert tracker.speed("job-1") == 0.0

    tracker.record("job-1", 0)
    tracker.record("job-1", 2_000)
    assert tracker.speed("job-1") > 0.0
    tracker.discard("job-1")
    assert tracker.speed("job-1") == 0.0
