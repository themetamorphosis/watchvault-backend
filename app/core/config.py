from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator, model_validator


MIN_SECRET_KEY_LENGTH = 32

# Substrings that mark a key as a placeholder. Matched as substrings, not as an
# exact-match set: the previous exact-match blocklist let near-misses straight
# through — "change-me-in-production" (the docker-compose default) matched
# neither "change-me" nor "your-super-secret-key-change-in-production".
PLACEHOLDER_SECRET_MARKERS = (
    "change",
    "secret-key",
    "example",
    "placeholder",
    "dummy",
    "insecure",
    "your-",
)

_GENERATE_HINT = (
    'generate one with: python -c "import secrets; print(secrets.token_urlsafe(64))"'
)

HARDENED_ENVIRONMENTS = {"production", "staging"}


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
        """Length and presence apply in every environment."""
        candidate = (v or "").strip()

        if not candidate:
            raise ValueError(f"SECRET_KEY must be set — {_GENERATE_HINT}")

        if len(candidate) < MIN_SECRET_KEY_LENGTH:
            raise ValueError(
                f"SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} characters "
                f"(got {len(candidate)}) — {_GENERATE_HINT}"
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

    @model_validator(mode="after")
    def reject_placeholder_secret_outside_dev(self) -> "Settings":
        """Refuse to boot production/staging with a recognisable placeholder key.

        Scoped to deployed environments so that development and CI can keep using
        readable keys (which necessarily contain words like "test").
        """
        if self.ENVIRONMENT.lower() not in HARDENED_ENVIRONMENTS:
            return self

        lowered = self.SECRET_KEY.strip().lower()
        for marker in PLACEHOLDER_SECRET_MARKERS:
            if marker in lowered:
                raise ValueError(
                    f"SECRET_KEY looks like a placeholder (contains {marker!r}) and "
                    f"ENVIRONMENT is {self.ENVIRONMENT!r} — {_GENERATE_HINT}"
                )

        return self


settings = Settings()
