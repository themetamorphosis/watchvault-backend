from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator


class Settings(BaseSettings):
    PROJECT_NAME: str = "Things Ive Watched API"
    API_V1_STR: str = "/api/v1"
    FRONTEND_URL: str = "http://localhost:3000"
    ENVIRONMENT: str = "development"  # "development" | "staging" | "production"

    SECRET_KEY: str = ""
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours (was 7 days — too long)
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    DATABASE_URL: str = ""

    TMDB_API_KEY: str = ""

    UPLOAD_DIR: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("SECRET_KEY")
    @classmethod
    def secret_key_required(cls, v: str) -> str:
        _blocked = {"your-super-secret-key-change-in-production", "", "change-me", "secret"}
        if not v or v.lower().strip() in _blocked:
            raise ValueError(
                "SECRET_KEY must be set to a real value — "
                "generate one with: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
            )
        return v

    @field_validator("DATABASE_URL")
    @classmethod
    def database_url_required(cls, v: str) -> str:
        if not v:
            raise ValueError("DATABASE_URL must be set in environment or .env file")
        return v

    @field_validator("TMDB_API_KEY")
    @classmethod
    def tmdb_key_required(cls, v: str) -> str:
        if not v:
            raise ValueError("TMDB_API_KEY must be set in environment or .env file")
        return v


settings = Settings()
