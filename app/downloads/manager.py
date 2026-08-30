from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
import logging
from pathlib import Path
import shutil
import time
import uuid

from app.config import Settings
from app.db import Database
from app.models import Job
from app.downloads.worker import run_download_job

logger = logging.getLogger(__name__)

_SPEED_WINDOW_SECONDS = 15.0
_SPEED_MIN_ELAPSED_SECONDS = 0.5


class SpeedTracker:
    # Tracks a short rolling window of (monotonic_time, bytes_done) samples
    # per job so speed reflects recent throughput rather than the lifetime
    # average, which would understate speed after a slow start (e.g. yt-dlp
    # resolving the stream) and throw off the ETA derived from it.
    def __init__(self) -> None:
        self._samples: dict[str, deque[tuple[float, int]]] = {}

    def record(self, job_id: str, bytes_done: int) -> None:
        samples = self._samples.setdefault(job_id, deque())
        now = time.monotonic()
        samples.append((now, bytes_done))
        cutoff = now - _SPEED_WINDOW_SECONDS
        while len(samples) > 1 and samples[0][0] < cutoff:
            samples.popleft()

    def speed(self, job_id: str) -> float:
        samples = self._samples.get(job_id)
        if not samples or len(samples) < 2:
            return 0.0
        first_time, first_bytes = samples[0]
        last_time, last_bytes = samples[-1]
        elapsed = last_time - first_time
        if elapsed < _SPEED_MIN_ELAPSED_SECONDS:
            return 0.0
        return max(0.0, (last_bytes - first_bytes) / elapsed)

    def reset(self, job_id: str) -> None:
        self._samples.pop(job_id, None)

    def discard(self, job_id: str) -> None:
        self._samples.pop(job_id, None)


class DownloadManager:
    def __init__(self, settings: Settings, db: Database) -> None:
        self._settings = settings
        self._db = db
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._running = False
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._watchdog_task: asyncio.Task | None = None
        self._speed = SpeedTracker()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        worker_count = max(self._settings.max_concurrent_downloads, 1)
        for _ in range(worker_count):
            self._workers.append(asyncio.create_task(self._worker_loop()))
        await self._recover_stuck_jobs()
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        logger.info("Download manager started with %d worker(s)", worker_count)

    async def _recover_stuck_jobs(self) -> None:
        # Jobs left in "queued"/"resolving"/"downloading" state after a crash
        # or restart have no process backing them anymore (the process
        # registry starts empty) and would otherwise sit stuck forever since
        # nothing re-populates the in-memory queue on its own.
        in_flight_states = {"queued", "resolving", "downloading"}
        for job in self._db.list_jobs():
            if job.state in in_flight_states:
                logger.warning(
                    "Recovering job id=%s infohash=%s stuck in state=%s after restart, re-queuing",
                    job.id,
                    job.infohash,
                    job.state,
                )
                self._db.update_job_state(job.id, state="queued", progress=0.0, error="", error_kind="")
                self._speed.reset(job.id)
                await self._queue.put(job.id)

    async def _watchdog_loop(self) -> None:
        # Jobs that exhausted _run_job's fast retry loop are left in
        # state="error" rather than retried forever in-place, because doing
        # the retry sleep inside a worker task would occupy one of the fixed
        # worker slots for the whole backoff window and starve other queued
        # jobs. This single background loop re-queues them instead, with a
        # backoff computed from their persisted retry_count so it survives
        # restarts. It also runs its first scan immediately, before the first
        # sleep, so a job already stuck in error="transient" from before a
        # container restart is picked up right away with no separate
        # crash-recovery special-casing needed.
        interval = max(self._settings.download_watchdog_interval, 1.0)
        while self._running:
            try:
                await self._retry_stalled_error_jobs()
            except Exception:
                logger.exception("Watchdog scan failed")
            await asyncio.sleep(interval)

    async def _retry_stalled_error_jobs(self) -> None:
        now = datetime.now(timezone.utc)
        candidates = 0
        for job in self._db.list_jobs():
            if job.state != "error" or job.error_kind != "transient":
                continue
            candidates += 1
            backoff = self._compute_backoff(job.retry_count)
            elapsed = (now - datetime.fromisoformat(job.updated_at)).total_seconds()
            if elapsed < backoff:
                continue
            logger.warning(
                "Watchdog: re-queuing job id=%s infohash=%s after %.0fs cooldown "
                "(retry_count=%d, backoff=%.0fs, last error=%s)",
                job.id, job.infohash, elapsed, job.retry_count, backoff, job.error,
            )
            self._db.update_job_state(job.id, state="queued", progress=0.0, error="", error_kind="")
            self._speed.reset(job.id)
            await self._queue.put(job.id)
        logger.debug("Watchdog: scanned jobs, %d transient-error candidate(s) checked", candidates)

    def _compute_backoff(self, retry_count: int) -> float:
        exponent = min(max(retry_count, 0), 10)
        backoff = self._settings.download_retry_backoff_base * (2 ** exponent)
        return min(backoff, self._settings.download_retry_backoff_max)

    async def stop(self) -> None:
        self._running = False
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []
        if self._watchdog_task:
            self._watchdog_task.cancel()
            await asyncio.gather(self._watchdog_task, return_exceptions=True)
            self._watchdog_task = None
        logger.info("Download manager stopped")

    async def create_or_enqueue(self, *, infohash: str, category: str) -> Job:
        existing = self._db.get_job_by_infohash(infohash)
        if existing:
            if existing.state in {"paused", "error"}:
                logger.info("Re-enqueuing existing job id=%s (was %s) for infohash=%s", existing.id, existing.state, infohash)
                # A manual re-add is a deliberate fresh start: clear the
                # persisted failure history too, not just state/progress.
                self._db.update_job_state(
                    existing.id, state="queued", progress=0.0, error="", error_kind="", retry_count=0
                )
                self._speed.reset(existing.id)
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
        job = self._db.create_job(job_id, infohash, category, save_path, content_path, source=release.source)
        self._db.ensure_category(category)
        await self._queue.put(job.id)
        logger.info("Created job id=%s for infohash=%s release=%s category=%s", job.id, infohash, release.release_name, category)
        return job

    async def pause_hashes(self, hashes: list[str]) -> None:
        for hash_value in hashes:
            job = self._db.get_job_by_infohash(hash_value)
            if job and job.state in {"queued", "downloading", "resolving"}:
                logger.info("Pausing job id=%s infohash=%s", job.id, hash_value)
                # Set the paused state before terminating the process, so the
                # worker's post-termination state check sees "paused" and does
                # not overwrite it with a completed/error outcome.
                self._db.update_job_state(job.id, state="paused")
                self._speed.reset(job.id)
                self._terminate_process(job.id)

    async def resume_hashes(self, hashes: list[str]) -> None:
        for hash_value in hashes:
            job = self._db.get_job_by_infohash(hash_value)
            if job and job.state == "paused":
                logger.info("Resuming job id=%s infohash=%s", job.id, hash_value)
                self._db.update_job_state(job.id, state="queued", error="")
                await self._queue.put(job.id)

    async def delete_hashes(self, hashes: list[str], *, delete_files: bool = False) -> None:
        for hash_value in hashes:
            job = self._db.get_job_by_infohash(hash_value)
            if not job:
                continue
            self._terminate_process(job.id)
            self._speed.discard(job.id)
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
            except Exception as exc:
                logger.exception("Unhandled error while running job id=%s", job_id)
                self._db.record_job_failure(job_id, error=str(exc), error_kind="transient")
            finally:
                self._queue.task_done()

    async def _run_job(self, job_id: str) -> None:
        job = self._db.get_job(job_id)
        if not job or job.state == "paused":
            return
        release = self._db.get_release(job.infohash)
        if not release:
            logger.error("Job id=%s references missing release infohash=%s", job_id, job.infohash)
            self._db.record_job_failure(job_id, error="Release not found", error_kind="permanent")
            return
        self._db.update_job_state(job_id, state="resolving", error="")
        max_attempts = self._settings.max_retries + 1
        for attempt in range(max_attempts):
            logger.info("Running job id=%s infohash=%s attempt=%d/%d release=%s", job_id, job.infohash, attempt + 1, max_attempts, release.release_name)
            await run_download_job(
                self._settings,
                self._db,
                self._db.get_job(job_id) or job,
                release,
                self._processes,
                self._speed,
                attempt=attempt + 1,
                max_attempts=max_attempts,
            )
            refreshed = self._db.get_job(job_id)
            if refreshed is None or refreshed.state == "paused":
                return
            if refreshed.state == "completed":
                logger.info("Job id=%s completed successfully", job_id)
                return
            if attempt < self._settings.max_retries:
                logger.warning(
                    "Job id=%s infohash=%s failed on attempt %d/%d (error=%s), retrying",
                    job_id,
                    job.infohash,
                    attempt + 1,
                    max_attempts,
                    refreshed.error if refreshed else "unknown",
                )
                self._db.update_job_state(job_id, state="queued", progress=0.0)
                self._speed.reset(job_id)
                await asyncio.sleep(min(1 + attempt, 3))
                continue
            logger.error(
                "Job id=%s infohash=%s exhausted %d fast attempt(s) (error=%s, error_kind=%s); "
                "leaving for background watchdog to retry with backoff if transient",
                job_id, job.infohash, max_attempts,
                refreshed.error if refreshed else "unknown",
                refreshed.error_kind if refreshed else "unknown",
            )
            break

    def get_speed(self, job_id: str) -> float:
        return self._speed.speed(job_id)
