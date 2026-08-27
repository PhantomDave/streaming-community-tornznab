from __future__ import annotations

import logging

from app.config import settings
from app.models import Title
from app.sc.client import StreamingCommunityClient

logger = logging.getLogger(__name__)


async def search_titles(client: StreamingCommunityClient, query: str) -> list[Title]:
    if not query.strip():
        return []
    # Use locale-prefixed search endpoint (e.g., /it/search); the site renders
    # full HTML with the Inertia page payload embedded in a data-page attribute.
    search_path = f"/{settings.locale}/search"
    payload = await client.get_inertia_page(search_path, params={"q": query})
    # Extract titles from Inertia props structure
    entries = _extract_titles_from_inertia(payload)
    logger.debug("SC search %r returned %d raw entries", query, len(entries))
    titles: list[Title] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        sc_id = item.get("id")
        slug = item.get("slug")
        name = item.get("name") or item.get("title")
        sc_type = item.get("type", "movie")
        if not isinstance(sc_id, int) or not isinstance(slug, str) or not isinstance(name, str):
            continue
        year = _extract_year(item)
        tmdb_id = item.get("tmdb_id")
        if not isinstance(tmdb_id, int):
            tmdb_id = None
        titles.append(Title(sc_id=sc_id, slug=slug, name=name, sc_type=sc_type, year=year, tmdb_id=tmdb_id))
    logger.info("SC search %r matched %d title(s)", query, len(titles))
    return titles


def _extract_titles_from_inertia(payload: dict) -> list:
    """Extract titles from Inertia.js response structure.
    
    Inertia response format:
    {
        "component": "...",
        "props": {
            "titles": [...],
            ...
        },
        ...
    }
    """
    if not isinstance(payload, dict):
        return []
    props = payload.get("props", {})
    if not isinstance(props, dict):
        return []
    titles = props.get("titles", [])
    return titles if isinstance(titles, list) else []


def _extract_year(item: dict) -> int | None:
    raw_year = item.get("year")
    if isinstance(raw_year, int):
        return raw_year
    date_field = item.get("release_date") or item.get("first_air_date") or item.get("last_air_date")
    if isinstance(date_field, str) and len(date_field) >= 4 and date_field[:4].isdigit():
        return int(date_field[:4])
    return None
