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
        "che", "chi", "non", "come",
        # Elided articles/prepositions ("dell'", "nell'", ...): the apostrophe
        # is a word boundary for _WORD_PATTERN, so these would otherwise slip
        # through as meaningless "distinctive" words.
        "dell", "nell", "sull", "dall", "quest",
        # English equivalents — queries aren't always Italian.
        "the", "an", "of", "on", "at", "to", "and", "or", "for",
    }
)
# How many of a query's most distinctive words to probe individually as a
# last-resort fallback; kept small since each one is a separate HTTP request.
_MAX_FALLBACK_WORDS = 5
# Segments shorter than this (e.g. the "Re" in "Re:Zero − Starting Life...")
# are too low-signal to be worth a dedicated probe.
_MIN_SEGMENT_LENGTH = 3

# AnimeUnity catalogs the dubbed version of every title as a separate entry
# whose title_eng carries a literal "(ITA)" suffix (e.g. "Your Name (ITA)").
# That suffix isn't a release tag we add — it's baked into the source title —
# but leaving it in breaks Sonarr/Radarr title matching (e.g. "Your.Name.ITA.
# 2016..." no longer resembles the movie's real title), causing rejections
# like "Unknown Movie. Unable to match to correct movie using release title."
# The dub/sub distinction is preserved separately via each variant's actual
# audio track, so the marker is redundant here and safe to strip.
_TRAILING_DUB_MARKER_PATTERN = re.compile(r"\s*\(\s*ita\s*\)\s*$", re.IGNORECASE)


def _strip_trailing_year(query: str) -> str:
    stripped = _TRAILING_YEAR_PATTERN.sub("", query).strip()
    return stripped or query


def _split_segments(query: str) -> list[str]:
    seen: set[str] = set()
    segments: list[str] = []
    for segment in _SEGMENT_SPLIT_PATTERN.split(query):
        segment = segment.strip()
        key = segment.lower()
        if len(segment) < _MIN_SEGMENT_LENGTH or key == query.lower() or key in seen:
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


def _fallback_candidates(query: str) -> list[str]:
    # A single-word segment (e.g. "Naruto" out of "Naruto - Shippuden") can
    # also turn up as a "distinctive word" for the same query; dedupe across
    # both lists so it isn't probed twice.
    seen: set[str] = set()
    candidates: list[str] = []
    for candidate in _split_segments(query) + _distinctive_words(query):
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    return candidates


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
    candidates = _fallback_candidates(query)
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


def _strip_dub_marker(name: str) -> str:
    stripped = _TRAILING_DUB_MARKER_PATTERN.sub("", name).strip()
    return stripped or name


def _significant_words(text: str) -> set[str]:
    return {w for w in _WORD_PATTERN.findall(text.lower()) if len(w) >= 3 and w not in _STOPWORDS_IT}


def _is_relevant(query_words: set[str], title: Title) -> bool:
    # AnimeUnity's own matching (both /livesearch itself and, transitively,
    # our word/segment fallback probes) isn't word-bounded — it happily
    # matches a query substring against the *middle* of an unrelated word
    # (e.g. Italian "vita" = "life" inside "graVITAtion"). Comparing whole
    # words instead of substrings filters that noise out while still keeping
    # genuine matches, since a real match shares an actual word, not just a
    # run of letters.
    if not query_words:
        return True
    candidate_words = _significant_words(f"{title.name} {title.slug}".replace("-", " "))
    return not query_words.isdisjoint(candidate_words)


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
        name = _strip_dub_marker(name)
        titles.append(
            Title(sc_id=au_id, slug=slug, name=name, sc_type=au_type, source="animeunity", year=_extract_year(item), tmdb_id=None)
        )
    query_words = _significant_words(search_query)
    relevant = [title for title in titles if _is_relevant(query_words, title)]
    dropped = len(titles) - len(relevant)
    if dropped:
        logger.info(
            "AnimeUnity search %r dropped %d irrelevant raw match(es): %s",
            query,
            dropped,
            [title.name for title in titles if title not in relevant],
        )
    logger.info("AnimeUnity search %r matched %d title(s)", query, len(relevant))
    return relevant


def _extract_year(item: dict) -> int | None:
    date_field = item.get("date")
    if isinstance(date_field, str) and len(date_field) >= 4 and date_field[:4].isdigit():
        return int(date_field[:4])
    return None
