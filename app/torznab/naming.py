from __future__ import annotations

import re

from app.models import Title

INVALID_CHARS = re.compile(r"[^A-Za-z0-9.\-]+")
MULTI_SPACES = re.compile(r"\s+")
# SC appends a localized subtitle to some titles ("The Other Bennet Sister:
# Orgoglio e riscatto", "Titolo - Sottotitolo").
SUBTITLE_SEPARATOR = re.compile(r"\s*:\s*|\s+-\s+")
MATCH_WORD = re.compile(r"[^\W_]+", re.UNICODE)
YEAR_WORD = re.compile(r"(19|20)\d{2}")
IGNORED_MATCH_WORDS = frozenset({"the", "a", "an"})


def normalize_title(raw: str) -> str:
    compact = MULTI_SPACES.sub(" ", raw).strip()
    compact = compact.replace(" ", ".")
    compact = INVALID_CHARS.sub(".", compact)
    compact = re.sub(r"\.+", ".", compact).strip(".")
    return compact or "Unknown.Title"


def release_title_for_query(name: str, query: str) -> str:
    """Drop a localized subtitle the *Arr app doesn't know about.

    Sonarr/Radarr reject a release whose title doesn't parse to the series
    or movie they searched for, so "The.Other.Bennet.Sister.Orgoglio.e.
    riscatto.S01E01..." comes back as "Unknown Series". When the words of
    the search query match exactly the part of the name before the
    subtitle, name the release after that part. Exact matching keeps real
    sequel titles intact ("Dune: Parte due" searched as "Dune Part Two"
    stays as is instead of becoming "Dune").
    """
    parts = SUBTITLE_SEPARATOR.split(name, maxsplit=1)
    if len(parts) < 2:
        return name
    query_words = _match_words(query)
    if query_words and query_words == _match_words(parts[0]):
        return parts[0]
    return name


def _match_words(text: str) -> set[str]:
    return {
        word
        for word in MATCH_WORD.findall(text.lower())
        if word not in IGNORED_MATCH_WORDS and not YEAR_WORD.fullmatch(word)
    }


def codec_from_variant(codecs: str) -> str:
    lowered = codecs.lower()
    if "hev1" in lowered or "hvc1" in lowered or "hevc" in lowered:
        return "H265"
    return "H264"


def audio_label(audio: str) -> str:
    lowered = audio.lower()
    has_ita = "ita" in lowered
    has_eng = "eng" in lowered
    if has_ita and has_eng:
        return "MULTi"
    if has_eng:
        return "ENG"
    if has_ita:
        return "ITA"
    # Neither an Italian nor an English track (e.g. a Japanese-only anime
    # dub): label it as subtitled rather than silently claiming ITA, which
    # would otherwise collide with a genuine Italian dub of the same title.
    return "SUB-ITA"


def build_release_name(
    *,
    title: Title,
    resolution: int,
    codecs: str,
    audio: str,
    season: int | None,
    episode: int | None,
    release_group: str,
) -> str:
    normalized = normalize_title(title.name)
    codec = codec_from_variant(codecs)
    audio_part = audio_label(audio)
    if title.sc_type.lower() == "tv" and season is not None and episode is not None:
        return (
            f"{normalized}.S{season:02d}E{episode:02d}."
            f"{resolution}p.WEB-DL.{codec}.{audio_part}-{release_group}"
        )
    year = title.year or 0
    if year > 0:
        return f"{normalized}.{year}.{resolution}p.WEB-DL.{codec}.{audio_part}-{release_group}"
    return f"{normalized}.{resolution}p.WEB-DL.{codec}.{audio_part}-{release_group}"
