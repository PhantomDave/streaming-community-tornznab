import asyncio

import pytest

from app.models import Episode, Title, Variant
from app.provider import Provider, ProviderRegistry


class _StubProvider(Provider):
    def __init__(self, source: str) -> None:
        self.source = source

    async def search_titles(self, query: str) -> list[Title]:
        return []

    async def get_title_details(self, id: int, slug: str) -> dict:
        return {}

    async def get_season_episodes(self, id: int, slug: str, season: int) -> list[Episode]:
        return []

    async def resolve_variants(self, id: int, slug: str, season: int | None, episode_id: int | None) -> list[Variant]:
        return []


def test_provider_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        Provider()  # type: ignore[abstract]


def test_registry_get_and_all() -> None:
    sc = _StubProvider("sc")
    registry = ProviderRegistry({"sc": sc})
    assert registry.get("sc") is sc
    assert registry.all() == [sc]


def test_registry_get_unknown_source_raises() -> None:
    registry = ProviderRegistry({})
    with pytest.raises(KeyError):
        registry.get("animeunity")


def test_registry_close_closes_every_provider() -> None:
    closed: list[str] = []

    class TrackingProvider(_StubProvider):
        async def close(self) -> None:
            closed.append(self.source)

    registry = ProviderRegistry({"sc": TrackingProvider("sc"), "animeunity": TrackingProvider("animeunity")})
    asyncio.run(registry.close())
    assert sorted(closed) == ["animeunity", "sc"]
