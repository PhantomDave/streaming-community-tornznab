from __future__ import annotations

from app.models import Episode, Title, Variant
from app.provider import Provider
from app.sc.client import StreamingCommunityClient
from app.sc.resolver import resolve_variants
from app.sc.search import search_titles
from app.sc.titles import get_season_episodes, get_title_details


class SCProvider(Provider):
    source = "sc"

    def __init__(self, client: StreamingCommunityClient) -> None:
        self._client = client

    async def search_titles(self, query: str) -> list[Title]:
        return await search_titles(self._client, query)

    async def get_title_details(self, id: int, slug: str) -> dict:
        return await get_title_details(self._client, id, slug)

    async def get_season_episodes(self, id: int, slug: str, season: int) -> list[Episode]:
        return await get_season_episodes(self._client, id, slug, season)

    async def resolve_variants(
        self, id: int, slug: str, season: int | None, episode_id: int | None
    ) -> list[Variant]:
        return await resolve_variants(self._client, sc_id=id, slug=slug, season=season, episode_id=episode_id)

    async def close(self) -> None:
        await self._client.close()
