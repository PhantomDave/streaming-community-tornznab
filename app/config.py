from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "sctorznab"
    app_version: str = "0.1.0"
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=9118, alias="PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    sc_base_url: str | None = Field(default=None, alias="SC_BASE_URL")
    public_url: str = Field(default="http://localhost:9118", alias="PUBLIC_URL")
    torznab_api_key: str = Field(default="change-me", alias="TORZNAB_API_KEY")
    qbit_username: str = Field(default="admin", alias="QBIT_USERNAME")
    qbit_password: str = Field(default="adminadmin", alias="QBIT_PASSWORD")
    download_path: str = Field(default="/data/downloads", alias="DOWNLOAD_PATH")


settings = Settings()
