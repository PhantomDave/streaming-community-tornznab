from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import uuid

from app.config import Settings
from app.db import Database
from app.models import Job
from app.downloads.worker import run_download_job

logger = logging.getLogger(__name__)


class DownloadManager:
    def __init__(self, settings: Settings, db: Database) -> None:
        self._settings = settings
        self._db = db
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        worker_count = max(self._settings.max_concurrent_downloads, 1)
        for _ in range(worker_count):
            self._workers.append(asyncio.create_task(self._worker_loop()))
        logger.info("Download manager started with %d worker(s)", worker_count)

    async def stop(self) -> None:
        self._running = False
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []
        logger.info("Download manager stopped")

    async def create_or_enqueue(self, *, infohash: str, category: str) -> Job:
        existing = self._db.get_job_by_infohash(infohash)
        if existing:
            if existing.state in {"paused", "error"}:
                logger.info("Re-enqueuing existing job id=%s (was %s) for infohash=%s", existing.id, existing.state, infohash)
                self._db.update_job_state(existing.id, state="queued", progress=0.0, error="")
                await self._queue.put(existing.id)
            else:
                logger.info("Job already tracked id=%s state=%s for infohash=%s, skipping enqueue", existing.id, existing.state, infohash)
            return self._db.get_job(existing.id) or existing
        release = self._db.get_release(infohash)
        if not release:
            logger.error("No cached release found for infohash=%s; was it ever returned by a Torznab search?", infohash)
            raise ValueError("Unknown release")
        save_path = str(Path(self._settings.download_path) / category)
        content_path = str(Path(save_path) / release.release_name / f"{release.release_name}.mkv")
        job_id = str(uuid.uuid4())
        job = self._db.create_job(job_id, infohash, category, save_path, content_path)
        self._db.ensure_category(category)
        await self._queue.put(job.id)
        logger.info("Created job id=%s for infohash=%s release=%s category=%s", job.id, infohash, release.release_name, category)
        return job

    async def pause_hashes(self, hashes: list[str]) -> None:
        for hash_value in hashes:
            job = self._db.get_job_by_infohash(hash_value)
            if job and job.state in {"queued", "downloading", "resolving"}:
                logger.info("Pausing job id=%s infohash=%s", job.id, hash_value)
                self._db.update_job_state(job.id, state="paused")

    async def resume_hashes(self, hashes: list[str]) -> None:
        for hash_value in hashes:
            job = self._db.get_job_by_infohash(hash_value)
            if job and job.state == "paused":
                logger.info("Resuming job id=%s infohash=%s", job.id, hash_value)
                self._db.update_job_state(job.id, state="queued", error="")
                await self._queue.put(job.id)

    async def delete_hashes(self, hashes: list[str]) -> None:
        self._db.delete_job(hashes)

    async def _worker_loop(self) -> None:
        while self._running:
            job_id = await self._queue.get()
            try:
                await self._run_job(job_id)
            except Exception:
                logger.exception("Unhandled error while running job id=%s", job_id)
            finally:
                self._queue.task_done()

    async def _run_job(self, job_id: str) -> None:
        job = self._db.get_job(job_id)
        if not job or job.state == "paused":
            return
        release = self._db.get_release(job.infohash)
        if not release:
            logger.error("Job id=%s references missing release infohash=%s", job_id, job.infohash)
            self._db.update_job_state(job_id, state="error", error="Release not found")
            return
        self._db.update_job_state(job_id, state="resolving", error="")
        for attempt in range(self._settings.max_retries + 1):
            logger.info("Running job id=%s attempt=%d/%d release=%s", job_id, attempt + 1, self._settings.max_retries + 1, release.release_name)
            await run_download_job(self._settings, self._db, self._db.get_job(job_id) or job, release)
            refreshed = self._db.get_job(job_id)
            if refreshed and refreshed.state == "completed":
                logger.info("Job id=%s completed successfully", job_id)
                return
            if attempt < self._settings.max_retries:
                logger.warning(
                    "Job id=%s failed on attempt %d/%d (error=%s), retrying",
                    job_id,
                    attempt + 1,
                    self._settings.max_retries + 1,
                    refreshed.error if refreshed else "unknown",
                )
                self._db.update_job_state(job_id, state="queued", progress=0.0)
                await asyncio.sleep(min(1 + attempt, 3))
                continue
            logger.error("Job id=%s exhausted all retries, giving up", job_id)
            break
