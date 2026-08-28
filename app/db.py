from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from app.models import Job, Release

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, db_path: str) -> None:
        self.db_path = self._resolve_db_path(db_path)
        self._lock = Lock()
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._init_schema()

    def _resolve_db_path(self, requested_path: str) -> str:
        parent = os.path.dirname(requested_path)
        if not parent:
            return requested_path
        # os.access() on a directory that doesn't exist yet always returns
        # False, which used to trip the "not writable" fallback below for
        # every fresh setup (e.g. a bind-mounted volume Docker hasn't
        # populated yet) even though the path is perfectly fine once
        # created. Create it first so only a genuine permission problem
        # (e.g. a volume mounted with the wrong owner) falls through.
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError:
            pass
        if os.access(parent, os.W_OK):
            return requested_path
        fallback_dir = os.path.join("/tmp", "sctorznab")
        os.makedirs(fallback_dir, exist_ok=True)
        fallback_path = os.path.join(fallback_dir, os.path.basename(requested_path))
        logger.warning(
            "DB path parent %s is not writable; falling back to %s. This location "
            "is NOT persisted across container restarts and job/download state "
            "will be lost — fix the permissions on the mounted volume.",
            parent,
            fallback_path,
        )
        return fallback_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS releases (
                    infohash TEXT PRIMARY KEY,
                    sc_id INTEGER NOT NULL,
                    sc_type TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    title TEXT NOT NULL,
                    year INTEGER,
                    season INTEGER,
                    episode INTEGER,
                    resolution INTEGER NOT NULL,
                    audio TEXT NOT NULL,
                    size_estimate INTEGER NOT NULL,
                    release_name TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    infohash TEXT NOT NULL,
                    category TEXT NOT NULL,
                    state TEXT NOT NULL,
                    progress REAL NOT NULL,
                    bytes_done INTEGER NOT NULL,
                    bytes_total INTEGER NOT NULL,
                    save_path TEXT NOT NULL,
                    content_path TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS categories (
                    name TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS title_cache (
                    key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS playlist_cache (
                    key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                """
            )

    def upsert_release(self, release: Release) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO releases (
                    infohash, sc_id, sc_type, slug, title, year, season, episode,
                    resolution, audio, size_estimate, release_name, source_url, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(infohash) DO UPDATE SET
                    source_url = excluded.source_url,
                    size_estimate = excluded.size_estimate,
                    release_name = excluded.release_name
                """,
                (
                    release.infohash,
                    release.sc_id,
                    release.sc_type,
                    release.slug,
                    release.title,
                    release.year,
                    release.season,
                    release.episode,
                    release.resolution,
                    release.audio,
                    release.size_estimate,
                    release.release_name,
                    release.source_url,
                    release.created_at,
                ),
            )

    def get_release(self, infohash: str) -> Release | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM releases WHERE infohash = ?", (infohash,)).fetchone()
        if not row:
            return None
        return Release(**dict(row))

    def list_releases(self, *, limit: int = 50, offset: int = 0) -> list[Release]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM releases ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [Release(**dict(row)) for row in rows]

    def create_job(self, job_id: str, infohash: str, category: str, save_path: str, content_path: str) -> Job:
        now = _utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    id, infohash, category, state, progress, bytes_done, bytes_total,
                    save_path, content_path, error, created_at, updated_at
                ) VALUES (?, ?, ?, 'queued', 0.0, 0, 0, ?, ?, NULL, ?, ?)
                """,
                (job_id, infohash, category, save_path, content_path, now, now),
            )
        job = self.get_job(job_id)
        if not job:
            raise RuntimeError("Unable to create job")
        return job

    def get_job(self, job_id: str) -> Job | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return Job(**dict(row)) if row else None

    def get_job_by_infohash(self, infohash: str) -> Job | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE infohash = ? ORDER BY created_at DESC LIMIT 1", (infohash,)).fetchone()
        return Job(**dict(row)) if row else None

    def list_jobs(self, category: str | None = None) -> list[Job]:
        sql = "SELECT * FROM jobs"
        args: tuple[Any, ...] = ()
        if category:
            sql += " WHERE category = ?"
            args = (category,)
        sql += " ORDER BY updated_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [Job(**dict(row)) for row in rows]

    def set_job_category(self, infohash: str, category: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET category = ?, updated_at = ? WHERE infohash = ?",
                (category, _utc_now(), infohash),
            )

    def update_job_state(
        self,
        job_id: str,
        *,
        state: str | None = None,
        progress: float | None = None,
        bytes_done: int | None = None,
        bytes_total: int | None = None,
        error: str | None = None,
        content_path: str | None = None,
    ) -> None:
        assignments: list[str] = []
        params: list[Any] = []
        if state is not None:
            assignments.append("state = ?")
            params.append(state)
        if progress is not None:
            assignments.append("progress = ?")
            params.append(progress)
        if bytes_done is not None:
            assignments.append("bytes_done = ?")
            params.append(bytes_done)
        if bytes_total is not None:
            assignments.append("bytes_total = ?")
            params.append(bytes_total)
        if error is not None:
            assignments.append("error = ?")
            params.append(error)
        if content_path is not None:
            assignments.append("content_path = ?")
            params.append(content_path)
        assignments.append("updated_at = ?")
        params.append(_utc_now())
        params.append(job_id)
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE jobs SET {', '.join(assignments)} WHERE id = ?", params)

    def delete_job(self, hashes: list[str]) -> None:
        if not hashes:
            return
        placeholders = ",".join(["?"] * len(hashes))
        with self._lock, self._connect() as conn:
            conn.execute(f"DELETE FROM jobs WHERE infohash IN ({placeholders})", hashes)

    def ensure_category(self, name: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO categories(name) VALUES (?)", (name,))

    def list_categories(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT name FROM categories ORDER BY name").fetchall()
        return [row["name"] for row in rows]

    def cache_get(self, table: str, key: str) -> Any | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT payload, expires_at FROM {table} WHERE key = ?",
                (key,),
            ).fetchone()
        if not row:
            return None
        if int(row["expires_at"]) < int(datetime.now(timezone.utc).timestamp()):
            self.cache_delete(table, key)
            return None
        return json.loads(row["payload"])

    def cache_set(self, table: str, key: str, payload: Any, ttl_seconds: int) -> None:
        expires = int(datetime.now(timezone.utc).timestamp()) + ttl_seconds
        with self._lock, self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {table}(key, payload, expires_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET payload = excluded.payload, expires_at = excluded.expires_at
                """,
                (key, json.dumps(payload), expires),
            )

    def cache_delete(self, table: str, key: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(f"DELETE FROM {table} WHERE key = ?", (key,))
