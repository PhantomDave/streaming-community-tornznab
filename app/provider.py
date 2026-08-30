"""Provider interface: the common contract every content-source adapter
(StreamingCommunity, AnimeUnity, ...) implements, plus a registry so the
Torznab/qBittorrent routers don't have to import a specific provider by name.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.config import Settings
from app.db import Database
from app.models import Episode, Title, Variant


class Provider(ABC):
    source: str

    @abstractmethod
    async def search_titles(self, query: str) -> list[Title]: ...

    @abstractmethod
    async def get_title_details(self, id: int, slug: str) -> dict: ...

    @abstractmethod
    async def get_season_episodes(self, id: int, slug: str, season: int) -> list[Episode]: ...

    @abstractmethod
    async def resolve_variants(
        self, id: int, slug: str, season: int | None, episode_id: int | None
    ) -> list[Variant]: ...

    async def close(self) -> None:
        return None


class ProviderRegistry:
    def __init__(self, providers: dict[str, Provider]) -> None:
        self._providers = providers

    def get(self, source: str) -> Provider:
        try:
            return self._providers[source]
        except KeyError:
            raise KeyError(f"Unknown provider source: {source}") from None

    def all(self) -> list[Provider]:
        return list(self._providers.values())

    async def close(self) -> None:
        for provider in self._providers.values():
            await provider.close()


def build_provider_registry(settings: Settings, db: Database) -> ProviderRegistry:
    from app.sc.client import StreamingCommunityClient
    from app.sc.provider import SCProvider

    providers: dict[str, Provider] = {"sc": SCProvider(StreamingCommunityClient(settings, db))}
    return ProviderRegistry(providers)
