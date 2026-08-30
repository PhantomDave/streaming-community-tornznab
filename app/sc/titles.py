from __future__ import annotations

import logging
from dataclasses import asdict

from app.config import settings
from app.models import Episode
from app.sc.client import StreamingCommunityClient

logger = logging.getLogger(__name__)


async def get_title_details(client: StreamingCommunityClient, sc_id: int, slug: str) -> dict:
    cache_key = f"sc:title:{sc_id}:{slug}"
    cached = client.get_cached(cache_key)
    if cached is not None:
        logger.debug("Title cache hit for %s", cache_key)
        return cached
    # Use locale-prefixed title endpoint (e.g., /it/titles/3-breaking-bad)
    title_path = f"/{settings.locale}/titles/{sc_id}-{slug}"
    payload = await client.get_inertia_page(title_path)
    client.set_cached(cache_key, payload, ttl=settings.title_cache_ttl)
    return payload


async def get_season_episodes(client: StreamingCommunityClient, sc_id: int, slug: str, season: int) -> list[Episode]:
    cache_key = f"sc:season:{sc_id}:{slug}:{season}"
    cached = client.get_cached(cache_key)
    if isinstance(cached, list):
        logger.debug("Season cache hit for %s", cache_key)
        return [Episode(**entry) for entry in cached if isinstance(entry, dict)]
    # Use locale-prefixed season endpoint
    season_path = f"/{settings.locale}/titles/{sc_id}-{slug}/season-{season}"
    payload = await client.get_inertia_page(season_path)
    episodes = _extract_episodes(payload)
    logger.info("SC title %s/%s season %d has %d episode(s)", sc_id, slug, season, len(episodes))
    client.set_cached(cache_key, [asdict(episode) for episode in episodes], ttl=settings.title_cache_ttl)
    return episodes


def _extract_episodes(payload: dict) -> list[Episode]:
    props = payload.get("props", {}) if isinstance(payload, dict) else {}
    loaded_season = props.get("loadedSeason", {}) if isinstance(props, dict) else {}
    episodes_data = loaded_season.get("episodes", []) if isinstance(loaded_season, dict) else []
    episodes: list[Episode] = []
    for item in episodes_data:
        if not isinstance(item, dict):
            continue
        episode_id = item.get("id")
        number = item.get("number")
        name = item.get("name", "")
        if isinstance(episode_id, int) and isinstance(number, int) and isinstance(name, str):
            episodes.append(Episode(id=episode_id, number=number, name=name))
    return episodes
