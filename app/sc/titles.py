from __future__ import annotations

from app.config import settings
from app.models import Episode
from app.sc.client import StreamingCommunityClient

INERTIA_HEADERS = {"X-Inertia": "true", "X-Inertia-Version": "1"}


async def get_title_details(client: StreamingCommunityClient, sc_id: int, slug: str) -> dict:
    cache_key = f"title:{sc_id}:{slug}"
    cached = client.get_cached(cache_key)
    if cached:
        return cached
    payload = await client.get_json(f"/titles/{sc_id}-{slug}", headers=INERTIA_HEADERS)
    if not isinstance(payload, dict):
        payload = {}
    client.set_cached(cache_key, payload, ttl=settings.title_cache_ttl)
    return payload


async def get_season_episodes(client: StreamingCommunityClient, sc_id: int, slug: str, season: int) -> list[Episode]:
    cache_key = f"season:{sc_id}:{slug}:{season}"
    cached = client.get_cached(cache_key)
    if isinstance(cached, list):
        return [Episode(**entry) for entry in cached if isinstance(entry, dict)]
    payload = await client.get_json(f"/titles/{sc_id}-{slug}/season-{season}", headers=INERTIA_HEADERS)
    episodes = _extract_episodes(payload)
    client.set_cached(cache_key, [episode.__dict__ for episode in episodes], ttl=settings.title_cache_ttl)
    return episodes


def _extract_episodes(payload: dict) -> list[Episode]:
    props = payload.get("props", {}) if isinstance(payload, dict) else {}
    episodes_data = props.get("episodes", []) if isinstance(props, dict) else []
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
