from __future__ import annotations

from app.models import Job, Release


def to_qbit_info(job: Job, release: Release | None) -> dict:
    size = max(job.bytes_total, release.size_estimate if release else 0)
    amount_left = max(size - job.bytes_done, 0)
    return {
        "hash": job.infohash,
        "name": release.release_name if release else job.infohash,
        "size": size,
        "progress": max(0.0, min(1.0, job.progress)),
        "state": _to_qbit_state(job.state),
        "save_path": job.save_path,
        "content_path": job.content_path,
        "category": job.category,
        "dlspeed": 0,
        "eta": 0 if job.progress >= 1.0 else 3600,
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
