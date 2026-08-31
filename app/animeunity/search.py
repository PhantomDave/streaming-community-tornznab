from __future__ import annotations

import logging
import re

from app.animeunity.client import AnimeUnityClient
from app.models import Title

logger = logging.getLogger(__name__)

# Radarr/Sonarr commonly append the release year to their search terms (e.g.
# "Title 2001" or "Title (2001)"), but AnimeUnity's own /livesearch returns
# zero matches for a query carrying a trailing year — it only matches on the
# bare title. Strip it before searching.
_TRAILING_YEAR_PATTERN = re.compile(r"\s*\(?(19|20)\d{2}\)?\s*$")


def _strip_trailing_year(query: str) -> str:
    stripped = _TRAILING_YEAR_PATTERN.sub("", query).strip()
    return stripped or query


async def search_titles(client: AnimeUnityClient, query: str) -> list[Title]:
    if not query.strip():
        return []
    search_query = _strip_trailing_year(query)
    payload = await client.post_json("/livesearch", json_body={"title": search_query})
    records = payload.get("records", []) if isinstance(payload, dict) else []
    logger.debug("AnimeUnity search %r (sent as %r) returned %d raw entries", query, search_query, len(records))
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
