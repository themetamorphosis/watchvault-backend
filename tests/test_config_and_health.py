"""Configuration validation and health probes.

The SECRET_KEY rules are a security control, so they get explicit tests: the
previous exact-match blocklist let the docker-compose default
("change-me-in-production") boot a real deployment with a committed key.
"""

import pytest

from app.core.config import Settings

BASE = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db",
    "TMDB_API_KEY": "key",
}

STRONG_KEY = "K7q" + "x" * 60


def _settings(**over) -> Settings:
    return Settings(**BASE, **over, _env_file=None)


# ═══════════════════════════════════════════════════════════════
#  SECRET_KEY
# ═══════════════════════════════════════════════════════════════

def test_strong_key_accepted_in_production():
    s = _settings(ENVIRONMENT="production", SECRET_KEY=STRONG_KEY)
    assert s.SECRET_KEY == STRONG_KEY


def test_empty_key_rejected():
    with pytest.raises(ValueError, match="SECRET_KEY must be set"):
        _settings(SECRET_KEY="")


@pytest.mark.parametrize("key", ["short", "abc123", "x" * 31])
def test_short_key_rejected_in_any_environment(key):
    with pytest.raises(ValueError, match="at least 32 characters"):
        _settings(ENVIRONMENT="development", SECRET_KEY=key)


@pytest.mark.parametrize(
    "key",
    [
        # The docker-compose default. Long enough to pass a length check,
        # and it matched no entry in the old exact-match blocklist.
        "change-me-in-production-padded-out-to-be-long",
        "your-super-secret-key-change-in-production",
        "example-key-that-is-long-enough-to-pass-length",
        "placeholder-key-that-is-long-enough-for-length",
    ],
)
def test_placeholder_keys_rejected_in_production(key):
    with pytest.raises(ValueError, match="placeholder"):
        _settings(ENVIRONMENT="production", SECRET_KEY=key)


def test_placeholder_keys_rejected_in_staging_too():
    with pytest.raises(ValueError, match="placeholder"):
        _settings(
            ENVIRONMENT="staging",
            SECRET_KEY="change-me-in-staging-but-long-enough-to-pass",
        )


def test_readable_key_allowed_in_development():
    """Dev and CI keep using descriptive keys; only deployed envs are strict."""
    s = _settings(
        ENVIRONMENT="development",
        SECRET_KEY="test-secret-key-for-testing-only-32chars!!",
    )
    assert s.ENVIRONMENT == "development"


# ═══════════════════════════════════════════════════════════════
#  Other required settings
# ═══════════════════════════════════════════════════════════════

def test_database_url_required():
    with pytest.raises(ValueError, match="DATABASE_URL"):
        Settings(
            DATABASE_URL="",
            TMDB_API_KEY="k",
            SECRET_KEY=STRONG_KEY,
            _env_file=None,
        )


def test_tmdb_key_required():
    with pytest.raises(ValueError, match="TMDB_API_KEY"):
        Settings(
            DATABASE_URL="postgresql+asyncpg://u:p@localhost/db",
            TMDB_API_KEY="",
            SECRET_KEY=STRONG_KEY,
            _env_file=None,
        )


# ═══════════════════════════════════════════════════════════════
#  Health probes
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_liveness_never_touches_dependencies(client):
    res = await client.get("/health/live")
    assert res.status_code == 200
    assert res.json() == {"status": "alive"}


@pytest.mark.asyncio
async def test_readiness_reports_database(client):
    res = await client.get("/health/ready")
    assert res.status_code == 200
    assert res.json()["checks"]["db"] == "ok"


@pytest.mark.asyncio
async def test_health_skips_tmdb_by_default(client):
    """TMDB used to be probed on every call, turning a 10s liveness loop into
    thousands of third-party requests a day."""
    res = await client.get("/health")
    assert res.status_code == 200
    assert "tmdb" not in res.json()["checks"]


@pytest.mark.asyncio
async def test_root_endpoint(client):
    res = await client.get("/")
    assert res.status_code == 200
    assert "message" in res.json()
