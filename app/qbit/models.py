from __future__ import annotations

from app.models import Job, Release


_ETA_UNKNOWN = 8640000  # qBittorrent's own sentinel for "infinite/unknown" ETA


def to_qbit_info(job: Job, release: Release | None, speed_bps: float = 0.0) -> dict:
    size = max(job.bytes_total, release.size_estimate if release else 0)
    amount_left = max(size - job.bytes_done, 0)
    if job.progress >= 1.0:
        eta = 0
    elif speed_bps > 0:
        eta = int(amount_left / speed_bps)
    else:
        eta = _ETA_UNKNOWN
    return {
        "hash": job.infohash,
        "name": release.release_name if release else job.infohash,
        "size": size,
        "progress": max(0.0, min(1.0, job.progress)),
        "state": _to_qbit_state(job.state),
        "save_path": job.save_path,
        "content_path": job.content_path,
        "category": job.category,
        "dlspeed": int(speed_bps),
        "eta": eta,
        "amount_left": amount_left,
    }


def to_qbit_properties(job: Job, release: Release | None) -> dict:
    return {
        "save_path": job.save_path,
        "total_size": max(job.bytes_total, release.size_estimate if release else 0),
        "progress": job.progress,
        "comment": "",
        "dl_limit": 0,
        "up_limit": 0,
    }


def _to_qbit_state(state: str) -> str:
    mapping = {
        "queued": "queuedDL",
        "resolving": "metaDL",
        "downloading": "downloading",
        "paused": "pausedDL",
        "completed": "pausedUP",
        "error": "error",
    }
    return mapping.get(state, "queuedDL")
