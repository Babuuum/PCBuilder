from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "PCBuilder API"
    environment: str = "local"
    debug: bool = False
    database_url: str = Field(
        default="postgresql+asyncpg://pcbuilder:pcbuilder@localhost:5432/pcbuilder",
        validation_alias="DATABASE_URL",
    )
    test_database_url: str = Field(
        default="postgresql+asyncpg://pcbuilder_test:pcbuilder_test@localhost:5433/pcbuilder_test",
        validation_alias="TEST_DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", validation_alias="REDIS_URL")
    celery_broker_url: str = Field(
        default="redis://localhost:6379/0", validation_alias="CELERY_BROKER_URL"
    )
    celery_result_backend: str = Field(
        default="redis://localhost:6379/1", validation_alias="CELERY_RESULT_BACKEND"
    )
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    browser_headless: bool = Field(default=True, validation_alias="BROWSER_HEADLESS")
    dns_browser_identities: str = Field(
        default="[]",
        validation_alias="DNS_BROWSER_IDENTITIES",
    )
    search_rate_limit: int = Field(default=10, validation_alias="SEARCH_RATE_LIMIT")
    search_rate_window_seconds: int = Field(
        default=60, validation_alias="SEARCH_RATE_WINDOW_SECONDS"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
