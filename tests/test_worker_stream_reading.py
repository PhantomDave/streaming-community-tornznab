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
            from pathlib import Path

            sys.stdout.write("Duration: 00:00:10.00, start: 0.0, bitrate: N/A\\n")
            sys.stdout.flush()
            for seconds in (2, 4, 6, 8):
                sys.stdout.write(f"frame=1 fps=1 time=00:00:{seconds:02d}.00 bitrate=1kbits/s\\r")
                sys.stdout.flush()
                time.sleep(0.05)
            sys.stdout.write("\\n")

            # Mimic yt-dlp actually producing an output file: find the "-o"
            # template argument and materialize it with the ".mkv" extension,
            # exactly as _ensure_mkv() expects to find it.
            output_arg = sys.argv[sys.argv.index("-o") + 1]
            output_path = Path(output_arg.replace("%(ext)s", "mkv"))
            output_path.write_text("fake video data")

            sys.exit(0)
            """
        ).strip()
        + "\n"
    )
    launcher = tmp_path / "fake_ytdlp"
    launcher.write_text(f"#!/usr/bin/env python3\n{script.read_text()}")
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return launcher


def _write_fake_ytdlp_no_output(tmp_path: Path) -> Path:
    # Mimics a disk-full/OOM-killed ffmpeg mux that still lets yt-dlp exit 0
    # without ever producing the final output file.
    script = tmp_path / "fake_ytdlp_no_output.py"
    script.write_text(
        textwrap.dedent(
            """
            import sys

            sys.stdout.write("Duration: 00:00:10.00, start: 0.0, bitrate: N/A\\n")
            sys.stdout.flush()
            sys.exit(0)
            """
        ).strip()
        + "\n"
    )
    launcher = tmp_path / "fake_ytdlp_no_output"
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


def _write_fake_ytdlp_two_tracks(tmp_path: Path) -> Path:
    # Mimics a release with separate video+audio HLS tracks (bv+ba): the
    # first "file" reaches 100%, then a second "[download]" progress
    # sequence starts over from 0% for the audio track. Percentage legitimately
    # drops between the two files even though real progress is being made.
    script = tmp_path / "fake_ytdlp.py"
    script.write_text(
        textwrap.dedent(
            """
            import sys
            import time
            from pathlib import Path

            for pct in (50, 100):
                sys.stdout.write(f"[download] {pct}.0% of 10.00MiB\\n")
                sys.stdout.flush()
                time.sleep(0.1)
            for pct in (10, 30, 60, 100):
                sys.stdout.write(f"[download] {pct}.0% of 2.00MiB\\n")
                sys.stdout.flush()
                time.sleep(0.1)

            # Mimic yt-dlp actually producing an output file: find the "-o"
            # template argument and materialize it with the ".mkv" extension,
            # exactly as _ensure_mkv() expects to find it.
            output_arg = sys.argv[sys.argv.index("-o") + 1]
            output_path = Path(output_arg.replace("%(ext)s", "mkv"))
            output_path.write_text("fake video data")

            sys.exit(0)
            """
        ).strip()
        + "\n"
    )
    launcher = tmp_path / "fake_ytdlp"
    launcher.write_text(f"#!/usr/bin/env python3\n{script.read_text()}")
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return launcher


def test_progress_drop_between_video_and_audio_tracks_is_not_a_stall(tmp_path) -> None:
    asyncio.run(_run_two_track_progress_drop_test(tmp_path))


async def _run_two_track_progress_drop_test(tmp_path) -> None:
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    settings = Settings(
        download_path=str(tmp_path / "downloads"),
        db_path=str(db_dir / "sctorznab.db"),
        ytdlp_path=str(_write_fake_ytdlp_two_tracks(tmp_path)),
        # Shorter than the total runtime of the fake process, but long
        # enough that only a genuine failure to reset the stall clock on
        # the video->audio progress drop would trigger a false kill.
        download_stall_timeout=0.4,
        download_progress_poll_interval=0.05,
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

    await run_download_job(settings, db, job, release)

    final = db.get_job(job.id)
    assert final is not None
    # Regression guard: previously the stall clock only reset on a strict
    # percentage *increase*, so the drop from 100% (video) to 10%/30%/60%
    # (audio) never counted as progress and the job was killed as "stalled"
    # even though it was actively downloading.
    assert final.state == "completed", final.error


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


def test_missing_output_file_is_reported_as_permanent_error(tmp_path) -> None:
    asyncio.run(_run_missing_output_test(tmp_path))


async def _run_missing_output_test(tmp_path) -> None:
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    settings = Settings(
        download_path=str(tmp_path / "downloads"),
        db_path=str(db_dir / "sctorznab.db"),
        ytdlp_path=str(_write_fake_ytdlp_no_output(tmp_path)),
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

    await run_download_job(settings, db, job, release)

    final = db.get_job(job.id)
    assert final is not None
    # Regression guard: _ensure_mkv previously touch()-created an empty file
    # and reported the job as "completed" when yt-dlp exited 0 without ever
    # producing real output (e.g. a disk-full/OOM-killed ffmpeg mux). It must
    # now surface this as a permanent error instead.
    assert final.state == "error"
    assert final.error_kind == "permanent"
