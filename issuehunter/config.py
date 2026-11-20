from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DeveloperLevel = Literal["beginner", "intermediate", "advanced"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # GitHub API
    github_token: str | None = None
    github_api_url: str = "https://api.github.com"

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:7b"

    # Storage and workspace
    database_path: Path = Path("data/issuehunter.sqlite3")
    workspace_path: Path = Path("workspace")

    # Application
    log_level: LogLevel = "INFO"
    request_timeout: float = Field(default=30.0, gt=0, le=10000)

    # Discovery preferences
    default_language: str = "python"
    default_developer_level: DeveloperLevel = "intermediate"
    max_issues_per_scan: int = Field(default=50, ge=1, le=1000)

    @field_validator("github_api_url", "ollama_base_url")
    @classmethod
    def validate_service_url(cls, value: str) -> str:
        """Ensure service URLs have a valid HTTP or HTTPS scheme and hostname."""
        parsed = urlsplit(value)

        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("URL must be a valid HTTP or HTTPS URL.")

        return value.rstrip("/")

    @field_validator("default_language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        """Prevent empty language preferences."""
        value = value.strip()

        if not value:
            raise ValueError("Default language cannot be empty.")

        return value.lower()

    def ensure_directories(self) -> None:
        """Create directories required by the application."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.workspace_path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings instance."""
    return Settings()
