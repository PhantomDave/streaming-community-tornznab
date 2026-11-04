import asyncio
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient

from app.config import settings
from app.deps import get_health_monitor, get_provider_registry
from app.health import HealthMonitor
from app.main import app
from app.models import Episode, Title, Variant
from app.provider import Provider, ProviderRegistry


class PingProvider(Provider):
    def __init__(self, source: str, error: Exception | None = None) -> None:
        self.source = source
        self.error = error
        self.searched: list[str] = []

    async def ping(self) -> None:
        if self.error:
            raise self.error

    async def search_titles(self, query: str) -> list[Title]:
        self.searched.append(query)
        return []

    async def get_title_details(self, id: int, slug: str) -> dict:
        return {}

    async def get_season_episodes(self, id: int, slug: str, season: int) -> list[Episode]:
        return []

    async def resolve_variants(self, id: int, slug: str, season: int | None, episode_id: int | None) -> list[Variant]:
        return []


def _monitor(*providers: Provider) -> HealthMonitor:
    monitor = HealthMonitor(ProviderRegistry({p.source: p for p in providers}), interval=0)
    asyncio.run(monitor.check_all())
    return monitor


def test_monitor_reports_failing_provider_with_root_cause() -> None:
    dead = PingProvider("sc", RuntimeError("SC request failed for /it/search"))
    dead.error.__cause__ = OSError("[Errno -2] Name does not resolve")
    monitor = _monitor(dead, PingProvider("animeunity"))

    assert set(monitor.failing()) == {"sc"}
    assert monitor.failing()["sc"].error == "[Errno -2] Name does not resolve"
    assert monitor.status()["animeunity"].ok


def test_monitor_recovers_when_provider_comes_back() -> None:
    provider = PingProvider("sc", RuntimeError("down"))
    monitor = _monitor(provider)
    assert monitor.failing()

    provider.error = None
    asyncio.run(monitor.check_all())
    assert not monitor.failing()


def test_torznab_search_returns_error_when_provider_down() -> None:
    provider = PingProvider("sc", RuntimeError("Name does not resolve"))
    monitor = _monitor(provider)
    app.dependency_overrides[get_health_monitor] = lambda: monitor
    app.dependency_overrides[get_provider_registry] = lambda: ProviderRegistry({"sc": provider})
    try:
        with TestClient(app) as client:
            # Empty query is what Prowlarr's indexer test and RSS sync send;
            # it must not be served from the release cache while SC is down.
            response = client.get("/torznab/api", params={"t": "search", "apikey": settings.torznab_api_key})
            caps = client.get("/torznab/api", params={"t": "caps"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    error = ET.fromstring(response.text)
    assert error.tag == "error"
    assert error.get("code") == "900"
    assert "sc unreachable: Name does not resolve" in error.get("description", "")
    assert provider.searched == []
    assert caps.status_code == 200


def test_health_endpoint_reports_provider_status() -> None:
    monitor = _monitor(PingProvider("sc", RuntimeError("down")), PingProvider("animeunity"))
    app.dependency_overrides[get_health_monitor] = lambda: monitor
    try:
        with TestClient(app) as client:
            response = client.get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["providers"]["sc"]["ok"] is False
    assert body["providers"]["animeunity"]["ok"] is True
