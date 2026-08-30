from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import Settings
from app.db import Database

logger = logging.getLogger(__name__)

_CSRF_META_PATTERN = re.compile(r'<meta name="csrf-token" content="([^"]+)"')


class AnimeUnityClient:
    def __init__(self, settings: Settings, db: Database) -> None:
        self._settings = settings
        self._db = db
        self._client = httpx.AsyncClient(
            base_url=settings.animeunity_base_url or "",
            timeout=settings.request_timeout,
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
        )
        self._csrf_token: str | None = None
        self._csrf_lock = asyncio.Lock()

    async def close(self) -> None:
        await self._client.aclose()

    async def get_json(self, path: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
        return await self._request("GET", path, mode="json", params=params, headers=headers)

    async def get_text(self, path: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> str:
        return await self._request("GET", path, mode="text", params=params, headers=headers)

    async def post_json(self, path: str, *, json_body: Any = None, headers: dict[str, str] | None = None) -> Any:
        return await self._request("POST", path, mode="json", json_body=json_body, headers=headers)

    async def _ensure_csrf(self) -> None:
        if self._csrf_token:
            return
        async with self._csrf_lock:
            if self._csrf_token:
                return
            response = await self._client.get("/")
            response.raise_for_status()
            match = _CSRF_META_PATTERN.search(response.text)
            if not match:
                raise RuntimeError("AnimeUnity did not return a csrf-token meta tag")
            self._csrf_token = match.group(1)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        mode: str,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        if not self._settings.animeunity_base_url:
            raise RuntimeError("ANIMEUNITY_BASE_URL is not configured")
        await self._ensure_csrf()
        retries = max(self._settings.max_retries, 0) + 1
        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                merged_headers = {
                    "X-CSRF-TOKEN": self._csrf_token or "",
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json",
                    **(headers or {}),
                }
                logger.debug("AnimeUnity request %s %s attempt=%d/%d", method, path, attempt, retries)
                response = await self._client.request(method, path, params=params, json=json_body, headers=merged_headers)
                if response.status_code == 403 and self._settings.flaresolverr_url:
                    logger.warning("AnimeUnity request %s got 403, attempting FlareSolverr challenge", path)
                    await self._solve_with_flaresolverr(str(response.request.url))
                    # A fresh cookie jar invalidates the previously scraped token.
                    self._csrf_token = None
                    await self._ensure_csrf()
                    merged_headers["X-CSRF-TOKEN"] = self._csrf_token or ""
                    response = await self._client.request(method, path, params=params, json=json_body, headers=merged_headers)
                response.raise_for_status()
                logger.debug("AnimeUnity request %s %s -> %d", method, path, response.status_code)
                return response.json() if mode == "json" else response.text
            except (httpx.HTTPError, ValueError) as exc:
                last_exc = exc
                logger.warning("AnimeUnity request %s %s failed on attempt %d/%d: %s", method, path, attempt, retries, exc)
                if attempt < retries:
                    await asyncio.sleep(min(0.5 * attempt, 2.0))
                    continue
                break
        logger.error("AnimeUnity request failed for %s after %d attempts: %s", path, retries, last_exc)
        raise RuntimeError(f"AnimeUnity request failed for {path}") from last_exc

    async def _solve_with_flaresolverr(self, url: str) -> None:
        """Solve a Cloudflare challenge via FlareResolverr and adopt its session.

        Same approach as StreamingCommunityClient._solve_with_flaresolverr:
        FlareResolverr drives a real headless browser through the challenge and
        returns the resulting cookies/User-Agent, which we copy onto our own
        client so subsequent direct requests to the same origin pass through.
        """
        flaresolverr_url = self._settings.flaresolverr_url
        if not flaresolverr_url:
            return
        timeout = self._settings.flaresolverr_timeout_ms / 1000 + 5
        logger.info("Solving Cloudflare challenge for %s via FlareSolverr at %s", url, flaresolverr_url)
        async with httpx.AsyncClient(timeout=timeout) as solver_client:
            response = await solver_client.post(
                flaresolverr_url,
                json={"cmd": "request.get", "url": url, "maxTimeout": self._settings.flaresolverr_timeout_ms},
            )
            response.raise_for_status()
            data = response.json()
        solution = data.get("solution", {}) if isinstance(data, dict) else {}
        user_agent = solution.get("userAgent")
        if user_agent:
            self._client.headers["User-Agent"] = user_agent
        fallback_domain = urlparse(url).hostname or ""
        cookie_count = 0
        for cookie in solution.get("cookies", []) or []:
            name = cookie.get("name")
            value = cookie.get("value")
            if name and value:
                domain = cookie.get("domain") or fallback_domain
                if domain:
                    self._client.cookies.set(name, value, domain=domain)
                else:
                    self._client.cookies.set(name, value)
                cookie_count += 1
        logger.info("FlareSolverr challenge solved for %s, adopted %d cookie(s)", url, cookie_count)

    def get_cached(self, cache_key: str, playlist: bool = False) -> Any | None:
        table = "playlist_cache" if playlist else "title_cache"
        return self._db.cache_get(table, cache_key)

    def set_cached(self, cache_key: str, payload: Any, *, ttl: int, playlist: bool = False) -> None:
        table = "playlist_cache" if playlist else "title_cache"
        self._db.cache_set(table, cache_key, payload, ttl)
