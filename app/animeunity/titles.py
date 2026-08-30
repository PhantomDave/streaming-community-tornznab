from __future__ import annotations

import logging
from dataclasses import asdict

from app.animeunity.client import AnimeUnityClient
from app.config import settings
from app.models import Episode

logger = logging.getLogger(__name__)

# AnimeUnity's /info_api endpoint rejects (returns an empty/uncounted payload
# for) any request whose start_range/end_range window exceeds 120 episodes —
# verified live (a window of 120 succeeds, 121 silently returns nothing).
# Long-running shows (e.g. Naruto's 220 episodes) need multiple requests.
_MAX_EPISODE_WINDOW = 120


async def get_title_details(client: AnimeUnityClient, id: int, slug: str) -> dict:
    cache_key = f"title:{id}:{slug}"
    cached = client.get_cached(cache_key)
    if cached is not None:
        logger.debug("Title cache hit for %s", cache_key)
        return cached
    payload = await client.get_json(f"/info_api/{id}/{slug}", params={"start_range": 1, "end_range": 1})
    client.set_cached(cache_key, payload, ttl=settings.title_cache_ttl)
    return payload


async def get_season_episodes(client: AnimeUnityClient, id: int, slug: str, season: int) -> list[Episode]:
    # AnimeUnity has no server-side season concept: /info_api/{id}/{slug}
    # returns one flat, paginated episode list against a single
    # episodes_count, and multi-season anime is usually split across
    # separate AnimeUnity IDs/slugs entirely rather than modeled as seasons
    # of one ID. `season` is accepted for interface parity with other
    # providers but otherwise ignored — we always fetch this ID's full,
    # flat episode range.
    cache_key = f"season:{id}:{slug}:{season}"
    cached = client.get_cached(cache_key)
    if isinstance(cached, list):
        logger.debug("Season cache hit for %s", cache_key)
        return [Episode(**entry) for entry in cached if isinstance(entry, dict)]

    probe = await client.get_json(f"/info_api/{id}/{slug}", params={"start_range": 1, "end_range": 1})
    total = probe.get("episodes_count", 0) if isinstance(probe, dict) else 0
    if not isinstance(total, int) or total <= 0:
        return []

    episodes: list[Episode] = []
    for window_start in range(1, total + 1, _MAX_EPISODE_WINDOW):
        window_end = min(window_start + _MAX_EPISODE_WINDOW - 1, total)
        payload = await client.get_json(f"/info_api/{id}/{slug}", params={"start_range": window_start, "end_range": window_end})
        episodes.extend(_extract_episodes(payload))
    logger.info("AnimeUnity title %s/%s has %d episode(s)", id, slug, len(episodes))
    client.set_cached(cache_key, [asdict(episode) for episode in episodes], ttl=settings.title_cache_ttl)
    return episodes


def _extract_episodes(payload: dict) -> list[Episode]:
    items = payload.get("episodes", []) if isinstance(payload, dict) else []
    episodes: list[Episode] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        episode_id = item.get("id")
        # AnimeUnity returns "number" as a string (e.g. "1"), unlike SC's int.
        try:
            number = int(item.get("number"))
        except (TypeError, ValueError):
            continue
        name = item.get("title") or item.get("name") or ""
        if isinstance(episode_id, int):
            episodes.append(Episode(id=episode_id, number=number, name=name))
    return episodes
