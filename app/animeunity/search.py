from __future__ import annotations

import asyncio
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

# Splits a "Show - Subtitle: Sub-subtitle, alt subtitle" query into its parts.
_SEGMENT_SPLIT_PATTERN = re.compile(r"\s*[-–:]\s*|\s*,\s*")
_WORD_PATTERN = re.compile(r"[^\W\d_]+", re.UNICODE)
_STOPWORDS_IT = frozenset(
    {
        "il", "lo", "la", "i", "gli", "le", "un", "uno", "una",
        "di", "del", "dello", "della", "dei", "degli", "delle",
        "e", "ed", "o", "a", "ad", "da", "in", "con", "su", "per", "tra", "fra",
        # Elided articles/prepositions ("dell'", "nell'", ...): the apostrophe
        # is a word boundary for _WORD_PATTERN, so these would otherwise slip
        # through as meaningless "distinctive" words.
        "dell", "nell", "sull", "dall", "quest",
    }
)
# How many of a query's most distinctive words to probe individually as a
# last-resort fallback; kept small since each one is a separate HTTP request.
_MAX_FALLBACK_WORDS = 5


def _strip_trailing_year(query: str) -> str:
    stripped = _TRAILING_YEAR_PATTERN.sub("", query).strip()
    return stripped or query


def _split_segments(query: str) -> list[str]:
    seen: set[str] = set()
    segments: list[str] = []
    for segment in _SEGMENT_SPLIT_PATTERN.split(query):
        segment = segment.strip()
        key = segment.lower()
        if not segment or key == query.lower() or key in seen:
            continue
        seen.add(key)
        segments.append(segment)
    return segments


def _distinctive_words(query: str) -> list[str]:
    seen: set[str] = set()
    words: list[str] = []
    query_key = query.lower()
    for word in _WORD_PATTERN.findall(query):
        key = word.lower()
        if len(word) < 3 or key in _STOPWORDS_IT or key in seen or key == query_key:
            continue
        seen.add(key)
        words.append(word)
    return sorted(words, key=len, reverse=True)[:_MAX_FALLBACK_WORDS]


async def _livesearch(client: AnimeUnityClient, title: str) -> list[dict]:
    payload = await client.post_json("/livesearch", json_body={"title": title})
    records = payload.get("records", []) if isinstance(payload, dict) else []
    return [record for record in records if isinstance(record, dict)]


async def _fallback_search(client: AnimeUnityClient, query: str) -> list[dict]:
    """AnimeUnity's /livesearch ranks by similarity to the stored title/
    title_eng rather than doing a plain substring match, and caps results at 8.
    That combination means a franchise prefix tacked onto an otherwise-matching
    subtitle (e.g. "Lupin III - Il sigillo di sangue, la sirena
    dell'eternità") can dilute the match below whatever it scores "Il sigillo
    di sangue" on its own, and a title translated loosely from the Japanese
    (Italian "Il ritorno di Pycal" vs. AnimeUnity's stored "Return of Pycal")
    won't match as a full phrase at all — only the shared proper noun "Pycal"
    will. Retry with each "Show - Subtitle" segment of the query and with its
    most distinctive individual words, merging whatever comes back so the
    right title ends up somewhere in the result set.

    Candidates are probed concurrently and independently: one candidate
    timing out shouldn't multiply this single search's latency by the number
    of candidates, and shouldn't discard results already found from the
    candidates that did succeed.
    """
    candidates = _split_segments(query) + _distinctive_words(query)
    results = await asyncio.gather(*(_livesearch(client, candidate) for candidate in candidates), return_exceptions=True)
    merged: dict[int, dict] = {}
    for candidate, result in zip(candidates, results):
        if isinstance(result, BaseException):
            logger.warning("AnimeUnity fallback probe %r failed: %s", candidate, result)
            continue
        for record in result:
            record_id = record.get("id")
            if isinstance(record_id, int) and record_id not in merged:
                merged[record_id] = record
    return list(merged.values())


async def search_titles(client: AnimeUnityClient, query: str) -> list[Title]:
    if not query.strip():
        return []
    search_query = _strip_trailing_year(query)
    records = await _livesearch(client, search_query)
    if not records:
        records = await _fallback_search(client, search_query)
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
