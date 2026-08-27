from __future__ import annotations

import asyncio
import html as html_lib
import json
import re
from typing import Any

import httpx

from app.config import Settings
from app.db import Database

# Matches the standard Inertia.js SSR bootstrap: <div id="app" data-page="...json...">
_DATA_PAGE_PATTERN = re.compile(r'data-page="([^"]+)"')


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

    async def get_inertia_page(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Fetch a page rendered by Inertia.js and extract its embedded page payload.

        The site renders full HTML on first load and embeds the Inertia "page"
        object (component, props, version, ...) in a `data-page` attribute.
        This avoids relying on the fragile `X-Inertia`/`X-Inertia-Version`
        headers, which return 409 Conflict unless the exact current version
        is supplied.
        """
        html_text = await self.get_text(path, params=params)
        match = _DATA_PAGE_PATTERN.search(html_text)
        if not match:
            raise RuntimeError(f"SC request for {path} did not include an Inertia data-page payload")
        raw = html_lib.unescape(match.group(1))
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise RuntimeError(f"SC request for {path} returned an invalid Inertia payload") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"SC request for {path} returned an unexpected Inertia payload shape")
        return payload

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
                if response.status_code == 403 and self._settings.flaresolverr_url:
                    await self._solve_with_flaresolverr(str(response.request.url))
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

    async def _solve_with_flaresolverr(self, url: str) -> None:
        """Solve a Cloudflare challenge via FlareResolverr and adopt its session.

        FlareResolverr drives a real headless browser through the challenge and
        returns the resulting cookies/User-Agent, which we copy onto our own
        client so subsequent direct requests to the same origin pass through.
        """
        flaresolverr_url = self._settings.flaresolverr_url
        if not flaresolverr_url:
            return
        timeout = self._settings.flaresolverr_timeout_ms / 1000 + 5
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
        for cookie in solution.get("cookies", []) or []:
            name = cookie.get("name")
            value = cookie.get("value")
            if name and value:
                self._client.cookies.set(name, value, domain=cookie.get("domain") or "")

    def get_cached(self, cache_key: str, playlist: bool = False) -> Any | None:
        table = "playlist_cache" if playlist else "title_cache"
        return self._db.cache_get(table, cache_key)

    def set_cached(self, cache_key: str, payload: Any, *, ttl: int, playlist: bool = False) -> None:
        table = "playlist_cache" if playlist else "title_cache"
        self._db.cache_set(table, cache_key, payload, ttl)
