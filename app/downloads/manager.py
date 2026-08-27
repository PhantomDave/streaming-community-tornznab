from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import uuid

from app.config import Settings
from app.db import Database
from app.models import Job
from app.downloads.worker import run_download_job


class DownloadManager:
    def __init__(self, settings: Settings, db: Database) -> None:
        self._settings = settings
        self._db = db
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._running = False
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        for _ in range(max(self._settings.max_concurrent_downloads, 1)):
            self._workers.append(asyncio.create_task(self._worker_loop()))

    async def stop(self) -> None:
        self._running = False
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []

    async def create_or_enqueue(self, *, infohash: str, category: str) -> Job:
        existing = self._db.get_job_by_infohash(infohash)
        if existing:
            if existing.state in {"paused", "error"}:
                self._db.update_job_state(existing.id, state="queued", progress=0.0, error="")
                await self._queue.put(existing.id)
            return self._db.get_job(existing.id) or existing
        release = self._db.get_release(infohash)
        if not release:
            raise ValueError("Unknown release")
        save_path = str(Path(self._settings.download_path) / category)
        content_path = str(Path(save_path) / release.release_name / f"{release.release_name}.mkv")
        job_id = str(uuid.uuid4())
        job = self._db.create_job(job_id, infohash, category, save_path, content_path)
        self._db.ensure_category(category)
        await self._queue.put(job.id)
        return job

    async def pause_hashes(self, hashes: list[str]) -> None:
        for hash_value in hashes:
            job = self._db.get_job_by_infohash(hash_value)
            if job and job.state in {"queued", "downloading", "resolving"}:
                # Set the paused state before terminating the process, so the
                # worker's post-termination state check sees "paused" and does
                # not overwrite it with a completed/error outcome.
                self._db.update_job_state(job.id, state="paused")
                self._terminate_process(job.id)

    async def resume_hashes(self, hashes: list[str]) -> None:
        for hash_value in hashes:
            job = self._db.get_job_by_infohash(hash_value)
            if job and job.state == "paused":
                self._db.update_job_state(job.id, state="queued", error="")
                await self._queue.put(job.id)

    async def delete_hashes(self, hashes: list[str], *, delete_files: bool = False) -> None:
        for hash_value in hashes:
            job = self._db.get_job_by_infohash(hash_value)
            if not job:
                continue
            self._terminate_process(job.id)
            if delete_files:
                self._remove_job_files(job)
        self._db.delete_job(hashes)

    def _terminate_process(self, job_id: str) -> None:
        process = self._processes.get(job_id)
        if process is None or process.returncode is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            pass

    def _remove_job_files(self, job: Job) -> None:
        target = Path(job.content_path).parent
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)

    async def _worker_loop(self) -> None:
        while self._running:
            job_id = await self._queue.get()
            try:
                await self._run_job(job_id)
            finally:
                self._queue.task_done()

    async def _run_job(self, job_id: str) -> None:
        job = self._db.get_job(job_id)
        if not job or job.state == "paused":
            return
        release = self._db.get_release(job.infohash)
        if not release:
            self._db.update_job_state(job_id, state="error", error="Release not found")
            return
        self._db.update_job_state(job_id, state="resolving", error="")
        for attempt in range(self._settings.max_retries + 1):
            await run_download_job(
                self._settings,
                self._db,
                self._db.get_job(job_id) or job,
                release,
                self._processes,
            )
            refreshed = self._db.get_job(job_id)
            if refreshed is None or refreshed.state in {"completed", "paused"}:
                return
            if attempt < self._settings.max_retries:
                self._db.update_job_state(job_id, state="queued", progress=0.0)
                await asyncio.sleep(min(1 + attempt, 3))
                continue
            break
