from __future__ import annotations

from app.models import Title
from app.sc.client import StreamingCommunityClient


async def search_titles(client: StreamingCommunityClient, query: str) -> list[Title]:
    if not query.strip():
        return []
    payload = await client.get_json("/api/search", params={"q": query})
    entries = payload.get("data", []) if isinstance(payload, dict) else []
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
    return titles


def _extract_year(item: dict) -> int | None:
    raw_year = item.get("year")
    if isinstance(raw_year, int):
        return raw_year
    date_field = item.get("release_date") or item.get("first_air_date") or item.get("last_air_date")
    if isinstance(date_field, str) and len(date_field) >= 4 and date_field[:4].isdigit():
        return int(date_field[:4])
    return None
