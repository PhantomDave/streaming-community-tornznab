from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class Title:
    sc_id: int
    slug: str
    name: str
    sc_type: str
    year: int | None = None
    tmdb_id: int | None = None


@dataclass(slots=True)
class Episode:
    id: int
    number: int
    name: str


@dataclass(slots=True)
class Variant:
    resolution: int
    bandwidth: int | None
    url: str
    codecs: str = ""
    audio: str = "ITA"
    audio_url: str = ""


@dataclass(slots=True)
class Release:
    infohash: str
    sc_id: int
    sc_type: str
    slug: str
    title: str
    year: int | None
    season: int | None
    episode: int | None
    resolution: int
    audio: str
    size_estimate: int
    release_name: str
    source_url: str
    created_at: str
    codecs: str = ""
    audio_url: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Job:
    id: str
    infohash: str
    category: str
    state: str
    progress: float
    bytes_done: int
    bytes_total: int
    save_path: str
    content_path: str
    error: str | None
    created_at: str
    updated_at: str
    retry_count: int = 0
    error_kind: str | None = None
    last_progress_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
