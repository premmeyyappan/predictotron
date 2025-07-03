from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql+asyncpg://predictotron:password@localhost:5432/predictotron"
    redis_url: str = "redis://localhost:6379/0"

    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    ingest_batch_size: int = 10_000
    ingest_concurrency: int = 4

    # WebSocket broadcast channel
    ws_channel: str = "market_updates"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


settings = Settings()
