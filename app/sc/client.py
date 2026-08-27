from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.config import Settings
from app.db import Database


class StreamingCommunityClient:
    def __init__(self, settings: Settings, db: Database) -> None:
        self._settings = settings
        self._db = db
        self._client = httpx.AsyncClient(
            base_url=settings.sc_base_url or "",
            timeout=settings.request_timeout,
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def get_json(self, path: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
        return await self._request("json", path, params=params, headers=headers)

    async def get_text(self, path: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> str:
        return await self._request("text", path, params=params, headers=headers)

    async def _request(
        self,
        mode: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        if not self._settings.sc_base_url:
            raise RuntimeError("SC_BASE_URL is not configured")
        retries = max(self._settings.max_retries, 0) + 1
        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                response = await self._client.get(path, params=params, headers=headers)
                response.raise_for_status()
                return response.json() if mode == "json" else response.text
            except (httpx.HTTPError, ValueError) as exc:
                last_exc = exc
                if attempt < retries:
                    await asyncio.sleep(min(0.5 * attempt, 2.0))
                    continue
                break
        raise RuntimeError(f"SC request failed for {path}") from last_exc

    def get_cached(self, cache_key: str, playlist: bool = False) -> Any | None:
        table = "playlist_cache" if playlist else "title_cache"
        return self._db.cache_get(table, cache_key)

    def set_cached(self, cache_key: str, payload: Any, *, ttl: int, playlist: bool = False) -> None:
        table = "playlist_cache" if playlist else "title_cache"
        self._db.cache_set(table, cache_key, payload, ttl)
