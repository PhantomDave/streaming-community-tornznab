"""Background keepalive for the provider sites.

StreamingCommunity and AnimeUnity move domains every few weeks. Without an
active probe a dead domain is invisible: empty-query searches (Prowlarr's
indexer test and RSS sync) are served from the release cache, and a failed
search just looks like "no results". The monitor pings every provider on an
interval so /health and the Torznab endpoint can report the failure, which
makes Prowlarr mark the indexer as failing and fire its health notifications.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging

from app.models import now_utc
from app.provider import ProviderRegistry

logger = logging.getLogger(__name__)


@dataclass
class ProviderHealth:
    ok: bool
    checked_at: str
    error: str = ""


class HealthMonitor:
    def __init__(self, registry: ProviderRegistry, interval: float) -> None:
        self._registry = registry
        self._interval = interval
        self._status: dict[str, ProviderHealth] = {}
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._interval <= 0 or self._task:
            return
        self._task = asyncio.create_task(self._loop())
        logger.info("Provider health monitor started (interval=%.0fs)", self._interval)

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def status(self) -> dict[str, ProviderHealth]:
        return dict(self._status)

    def failing(self) -> dict[str, ProviderHealth]:
        return {source: health for source, health in self._status.items() if not health.ok}

    async def check_all(self) -> None:
        for provider in self._registry.all():
            previous = self._status.get(provider.source)
            try:
                await provider.ping()
            except Exception as exc:
                error = str(exc.__cause__ or exc) or type(exc).__name__
                self._status[provider.source] = ProviderHealth(ok=False, checked_at=now_utc(), error=error)
                logger.error("Provider %s health check failed: %s", provider.source, error)
                continue
            self._status[provider.source] = ProviderHealth(ok=True, checked_at=now_utc())
            if previous and not previous.ok:
                logger.info("Provider %s is reachable again", provider.source)

    async def _loop(self) -> None:
        # First check runs immediately so a domain that died while the
        # container was down is reported right after startup.
        while True:
            try:
                await self.check_all()
            except Exception:
                logger.exception("Provider health check loop failed")
            await asyncio.sleep(self._interval)
