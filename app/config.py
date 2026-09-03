import secrets
from functools import cached_property

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "sctorznab"
    app_version: str = "0.1.0"
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=9118, alias="PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    user_agent: str = Field(
        default="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
        alias="USER_AGENT",
    )
    sc_base_url: str | None = Field(default=None, alias="SC_BASE_URL")
    animeunity_base_url: str | None = Field(default=None, alias="ANIMEUNITY_BASE_URL")
    public_url: str = Field(default="http://localhost:9118", alias="PUBLIC_URL")
    torznab_api_key: str = Field(default_factory=lambda: secrets.token_hex(16), alias="TORZNAB_API_KEY")
    qbit_username: str = Field(default="admin", alias="QBIT_USERNAME")
    qbit_password: str = Field(default="adminadmin", alias="QBIT_PASSWORD")
    download_path: str = Field(default="/data/downloads", alias="DOWNLOAD_PATH")
    db_path: str = Field(default="/data/db/sctorznab.db", alias="DB_PATH")
    tmdb_api_key: str = Field(default="", alias="TMDB_API_KEY")
    release_group: str = Field(default="SC", alias="RELEASE_GROUP")
    preferred_audio: str = Field(default="ita,eng", alias="PREFERRED_AUDIO")
    max_concurrent_downloads: int = Field(default=2, alias="MAX_CONCURRENT_DOWNLOADS")
    playlist_cache_ttl: int = Field(default=21600, alias="PLAYLIST_CACHE_TTL")
    title_cache_ttl: int = Field(default=3600, alias="TITLE_CACHE_TTL")
    ytdlp_path: str = Field(default="yt-dlp", alias="YTDLP_PATH")
    ffmpeg_path: str = Field(default="ffmpeg", alias="FFMPEG_PATH")
    max_retries: int = Field(default=2, alias="MAX_RETRIES")
    download_stall_timeout: float = Field(default=180.0, alias="DOWNLOAD_STALL_TIMEOUT")
    download_progress_poll_interval: float = Field(default=10.0, alias="DOWNLOAD_PROGRESS_POLL_INTERVAL")
    download_retry_backoff_base: float = Field(default=30.0, alias="DOWNLOAD_RETRY_BACKOFF_BASE")
    download_retry_backoff_max: float = Field(default=600.0, alias="DOWNLOAD_RETRY_BACKOFF_MAX")
    download_watchdog_interval: float = Field(default=60.0, alias="DOWNLOAD_WATCHDOG_INTERVAL")
    download_concurrent_fragments: int = Field(default=4, alias="DOWNLOAD_CONCURRENT_FRAGMENTS")
    verbose_downloads: bool = Field(default=True, alias="VERBOSE_DOWNLOADS")
    request_timeout: float = Field(default=20.0, alias="REQUEST_TIMEOUT")
    locale: str = Field(default="it", alias="SC_LOCALE")
    flaresolverr_url: str | None = Field(default=None, alias="FLARESOLVERR_URL")
    flaresolverr_timeout_ms: int = Field(default=60000, alias="FLARESOLVERR_TIMEOUT_MS")

    @cached_property
    def preferred_audio_list(self) -> list[str]:
        return [value.strip().lower() for value in self.preferred_audio.split(",") if value.strip()]

settings = Settings()
