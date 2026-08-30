from __future__ import annotations

import logging

from app.animeunity.client import AnimeUnityClient
from app.models import Title

logger = logging.getLogger(__name__)


async def search_titles(client: AnimeUnityClient, query: str) -> list[Title]:
    if not query.strip():
        return []
    payload = await client.post_json("/livesearch", json_body={"title": query})
    records = payload.get("records", []) if isinstance(payload, dict) else []
    logger.debug("AnimeUnity search %r returned %d raw entries", query, len(records))
    titles: list[Title] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        au_id = item.get("id")
        slug = item.get("slug")
        name = item.get("title_eng") or item.get("title")
        au_type = item.get("type") or "TV"
        if not isinstance(au_id, int) or not isinstance(slug, str) or not isinstance(name, str):
            continue
        titles.append(
            Title(sc_id=au_id, slug=slug, name=name, sc_type=au_type, source="animeunity", year=_extract_year(item), tmdb_id=None)
        )
    logger.info("AnimeUnity search %r matched %d title(s)", query, len(titles))
    return titles


def _extract_year(item: dict) -> int | None:
    date_field = item.get("date")
    if isinstance(date_field, str) and len(date_field) >= 4 and date_field[:4].isdigit():
        return int(date_field[:4])
    return None
