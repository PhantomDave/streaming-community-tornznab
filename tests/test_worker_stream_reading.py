from __future__ import annotations

import asyncio
import stat
import textwrap
from pathlib import Path

from app.config import Settings
from app.db import Database
from app.downloads.worker import run_download_job
from app.models import Release, now_utc


def _write_fake_ytdlp(tmp_path: Path) -> Path:
    # Mimics yt-dlp delegating an AES-128 HLS download to ffmpeg: a
    # "Duration:" line terminated with '\n', followed by several progress
    # updates that repaint the same line with '\r' only (no trailing '\n'),
    # exactly like real ffmpeg -stats output.
    script = tmp_path / "fake_ytdlp.py"
    script.write_text(
        textwrap.dedent(
            """
            import sys
            import time

            sys.stdout.write("Duration: 00:00:10.00, start: 0.0, bitrate: N/A\\n")
            sys.stdout.flush()
            for seconds in (2, 4, 6, 8):
                sys.stdout.write(f"frame=1 fps=1 time=00:00:{seconds:02d}.00 bitrate=1kbits/s\\r")
                sys.stdout.flush()
                time.sleep(0.05)
            sys.stdout.write("\\n")
            sys.exit(0)
            """
        ).strip()
        + "\n"
    )
    launcher = tmp_path / "fake_ytdlp"
    launcher.write_text(f"#!/usr/bin/env python3\n{script.read_text()}")
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return launcher


def _release() -> Release:
    return Release(
        infohash="deadbeef",
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
        release_name="Example.2024.1080p",
        source_url="https://example.test/playlist.m3u8",
        created_at=now_utc(),
    )


def test_carriage_return_only_progress_is_observed_before_process_exits(tmp_path) -> None:
    asyncio.run(_run_carriage_return_only_progress_test(tmp_path))


async def _run_carriage_return_only_progress_test(tmp_path) -> None:
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    settings = Settings(
        download_path=str(tmp_path / "downloads"),
        db_path=str(db_dir / "sctorznab.db"),
        ytdlp_path=str(_write_fake_ytdlp(tmp_path)),
        download_stall_timeout=5.0,
    )
    db = Database(settings.db_path)
    release = _release()
    db.upsert_release(release)
    job = db.create_job(
        "job-1",
        release.infohash,
        "radarr",
        str(tmp_path / "downloads" / "radarr"),
        str(tmp_path / "downloads" / "radarr" / release.release_name / f"{release.release_name}.mkv"),
    )

    seen_progress: list[float] = []
    original_update = db.update_job_state

    def _tracking_update(job_id: str, **kwargs) -> None:
        if "progress" in kwargs and kwargs["progress"] is not None:
            seen_progress.append(kwargs["progress"])
        original_update(job_id, **kwargs)

    db.update_job_state = _tracking_update  # type: ignore[method-assign]

    await run_download_job(settings, db, job, release)

    final = db.get_job(job.id)
    assert final is not None
    assert final.state == "completed"
    # Regression guard: with the old readline()-based reader, '\r'-only
    # ffmpeg progress updates never end a "line" (readline() only splits on
    # '\n'), so all four repaints stayed buffered together and surfaced as a
    # single batched update once the trailing '\n' finally arrived — instead
    # of one DB write per repaint as they actually happened. Require several
    # distinct intermediate values to prove each repaint was processed as it
    # arrived, not just that *a* non-terminal value snuck through.
    intermediate = {value for value in seen_progress if 0.0 < value < 1.0}
    assert len(intermediate) >= 3, seen_progress
