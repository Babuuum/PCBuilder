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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
