"""Environment-based application settings."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[3] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="Payment Pulse", validation_alias="APP_NAME")
    app_env: str = Field(default="development", validation_alias="APP_ENV")
    debug: bool = Field(default=False, validation_alias="DEBUG")
    host: str = Field(default="0.0.0.0", validation_alias="HOST")
    port: int = Field(default=8000, validation_alias="PORT")

    database_url: str | None = Field(default=None, validation_alias="DATABASE_URL")
    postgres_db: str = Field(default="payment_pulse", validation_alias="POSTGRES_DB")
    postgres_user: str = Field(default="payment_pulse", validation_alias="POSTGRES_USER")
    postgres_password: str = Field(default="", validation_alias="POSTGRES_PASSWORD")
    postgres_host: str = Field(default="localhost", validation_alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, validation_alias="POSTGRES_PORT")

    transaction_count: int = Field(default=500000, validation_alias="TRANSACTION_COUNT", ge=1)
    random_seed: int = Field(default=42, validation_alias="RANDOM_SEED")
    start_timestamp: str = Field(
        default="2026-01-01T00:00:00+00:00", validation_alias="START_TIMESTAMP"
    )
    transaction_frequency_seconds: int = Field(
        default=1, validation_alias="TRANSACTION_FREQUENCY_SECONDS", ge=1
    )

    @computed_field
    @property
    def resolved_database_url(self) -> str:
        """Return an explicit URL when supplied, otherwise build a PostgreSQL URL."""
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
