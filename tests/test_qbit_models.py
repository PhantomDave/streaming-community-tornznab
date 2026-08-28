from __future__ import annotations

from app.models import Job, Release, now_utc
from app.qbit.models import to_qbit_info


def _make_job(**overrides) -> Job:
    defaults = dict(
        id="job-1",
        infohash="hash1",
        category="radarr",
        state="downloading",
        progress=0.5,
        bytes_done=500,
        bytes_total=1000,
        save_path="/downloads/radarr",
        content_path="/downloads/radarr/file.mkv",
        error=None,
        created_at=now_utc(),
        updated_at=now_utc(),
    )
    defaults.update(overrides)
    return Job(**defaults)


def test_eta_is_amount_left_over_speed_when_speed_known() -> None:
    job = _make_job(bytes_done=500, bytes_total=1000)
    info = to_qbit_info(job, release=None, speed_bps=100.0)
    assert info["dlspeed"] == 100
    assert info["eta"] == 5  # 500 bytes left / 100 bytes/sec


def test_eta_is_unknown_sentinel_when_speed_is_zero() -> None:
    job = _make_job(progress=0.1)
    info = to_qbit_info(job, release=None, speed_bps=0.0)
    assert info["dlspeed"] == 0
    assert info["eta"] == 8640000


def test_eta_is_zero_once_job_completed_regardless_of_speed() -> None:
    job = _make_job(progress=1.0, bytes_done=1000, bytes_total=1000)
    info = to_qbit_info(job, release=None, speed_bps=0.0)
    assert info["eta"] == 0


def test_dlspeed_is_zeroed_for_completed_job_with_stale_tracker_speed() -> None:
    # A tracker's samples only get evicted on the next record() call, which
    # stops firing once a job leaves the downloading loop, so a stale
    # nonzero speed could otherwise linger past completion.
    job = _make_job(state="completed", progress=1.0, bytes_done=1000, bytes_total=1000)
    info = to_qbit_info(job, release=None, speed_bps=500_000.0)
    assert info["dlspeed"] == 0
    assert info["eta"] == 0


def test_dlspeed_is_zeroed_for_paused_job_with_stale_tracker_speed() -> None:
    job = _make_job(state="paused", progress=0.5, bytes_done=500, bytes_total=1000)
    info = to_qbit_info(job, release=None, speed_bps=500_000.0)
    assert info["dlspeed"] == 0
    assert info["eta"] == 8640000
