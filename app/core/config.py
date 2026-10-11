from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App settings, read from environment variables or the .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./claims.db"
    upload_dir: Path = Path("uploads")
    max_upload_mb: int = 10
    cors_origins: list[str] = ["http://localhost:5173"]

    # Auth
    secret_key: str
    access_token_expire_minutes: int = 60 * 12

    # First admin account, created on startup if it doesn't exist yet.
    admin_email: str = ""
    admin_password: str = ""

    # AI extraction
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-flash-lite"
    prompt_version: str = "extraction_v3"  # file name in app/prompts/ (without .txt)

    @field_validator("secret_key")
    @classmethod
    def secret_key_is_long(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("SECRET_KEY in .env must be at least 32 characters (see .env.example)")
        return value


settings = Settings()
