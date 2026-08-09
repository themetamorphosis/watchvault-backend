"""Phase 1 security hardening regressions.

Each test here corresponds to a finding in CODE_REVIEW_BACKEND.md and fails
against the pre-fix code for the documented reason.
"""

import uuid

import pytest
from httpx import AsyncClient

from app.core import security
from app.utils.file_validator import extension_for_content, sanitize_extension


# ── HIGH-1: bcrypt's 72-byte ceiling must surface as 422, never 500 ──────────


@pytest.mark.asyncio
async def test_register_rejects_password_over_bcrypt_limit(client: AsyncClient):
    """bcrypt 5.x raises on >72 bytes; without a schema cap that became a 500."""
    res = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"long-{uuid.uuid4().hex[:8]}@example.com",
            "name": "Long Password",
            "password": "A1!" + "x" * 100,
        },
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_register_accepts_password_at_exactly_72_bytes(client: AsyncClient):
    password = "A1!" + "x" * 69  # 72 bytes exactly
    assert len(password.encode("utf-8")) == 72
    res = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"exact-{uuid.uuid4().hex[:8]}@example.com",
            "name": "Boundary",
            "password": password,
        },
    )
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_register_counts_bytes_not_characters(client: AsyncClient):
    """Multi-byte characters consume more of bcrypt's budget than len() shows."""
    password = "A1!" + "é" * 40  # 43 characters, 83 bytes
    assert len(password) < 72 < len(password.encode("utf-8"))
    res = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"utf8-{uuid.uuid4().hex[:8]}@example.com",
            "name": "Multibyte",
            "password": password,
        },
    )
    assert res.status_code == 422


# ── HIGH-2: password change needs the current password and kills sessions ────


@pytest.mark.asyncio
async def test_password_change_requires_current_password(
    client: AsyncClient, auth_headers: dict
):
    res = await client.patch(
        "/api/v1/auth/me",
        headers=auth_headers,
        json={"password": "BrandNewPass123!"},
    )
    assert res.status_code == 400
    assert "current_password" in res.json()["detail"]


@pytest.mark.asyncio
async def test_password_change_rejects_wrong_current_password(
    client: AsyncClient, auth_headers: dict
):
    res = await client.patch(
        "/api/v1/auth/me",
        headers=auth_headers,
        json={
            "password": "BrandNewPass123!",
            "current_password": "NotTheRightPassword1!",
        },
    )
    assert res.status_code == 400
    assert "incorrect" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_password_change_succeeds_with_current_password(
    client: AsyncClient, auth_headers: dict, test_user, db
):
    res = await client.patch(
        "/api/v1/auth/me",
        headers=auth_headers,
        json={"password": "BrandNewPass123!", "current_password": "TestPass123!"},
    )
    assert res.status_code == 200

    await db.refresh(test_user)
    assert security.verify_password("BrandNewPass123!", test_user.password)


@pytest.mark.asyncio
async def test_password_change_revokes_outstanding_refresh_tokens(
    client: AsyncClient, test_user, db
):
    """Changing a password after a compromise must lock the attacker out."""
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": test_user.email, "password": "TestPass123!"},
    )
    assert login.status_code == 200
    tokens = login.json()

    changed = await client.patch(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
        json={"password": "BrandNewPass123!", "current_password": "TestPass123!"},
    )
    assert changed.status_code == 200

    replay = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert replay.status_code == 401


@pytest.mark.asyncio
async def test_profile_update_without_password_still_works(
    client: AsyncClient, auth_headers: dict
):
    """The only live caller sends {name, image} — it must not need a password."""
    res = await client.patch(
        "/api/v1/auth/me",
        headers=auth_headers,
        json={"name": "Renamed", "image": "/uploads/avatars/x.png"},
    )
    assert res.status_code == 200
    assert res.json()["name"] == "Renamed"


@pytest.mark.asyncio
async def test_password_change_enforces_byte_limit(
    client: AsyncClient, auth_headers: dict
):
    res = await client.patch(
        "/api/v1/auth/me",
        headers=auth_headers,
        json={"password": "A1!" + "x" * 100, "current_password": "TestPass123!"},
    )
    assert res.status_code == 422


# ── HIGH-3: stored extension comes from content, not the client filename ─────


def test_extension_is_derived_from_magic_bytes():
    assert extension_for_content(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8) == "png"
    assert extension_for_content(b"\xff\xd8\xff" + b"\x00" * 8) == "jpg"
    assert extension_for_content(b"GIF89a" + b"\x00" * 8) == "gif"
    assert extension_for_content(b"RIFF" + b"\x00" * 8) == "webp"


def test_extension_ignores_a_hostile_filename():
    """`evil.html` + a GIF magic prefix used to be stored as text/html."""
    assert sanitize_extension("evil.html") == "html"  # the old helper, unchanged
    assert extension_for_content(b"GIF89a<html><body>hi") == "gif"


@pytest.mark.asyncio
async def test_upload_stores_html_filename_as_image_extension(
    client: AsyncClient, auth_headers: dict
):
    res = await client.post(
        "/api/v1/upload",
        headers=auth_headers,
        files={"file": ("evil.html", b"GIF89a<html><body>pwned", "image/gif")},
    )
    assert res.status_code == 200
    image_url = res.json()["imageUrl"]
    assert image_url.endswith(".gif")
    assert ".html" not in image_url


@pytest.mark.asyncio
async def test_snapshot_stores_html_filename_as_image_extension(
    client: AsyncClient, auth_headers: dict
):
    res = await client.post(
        "/api/v1/snapshots",
        headers=auth_headers,
        files={"file": ("evil.html", b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, "image/png")},
    )
    assert res.status_code == 200
    assert res.json()["imageUrl"].endswith(".png")


# ── MEDIUM-5: sort_by is an allow-listed enum, not free text ─────────────────


@pytest.mark.asyncio
async def test_discover_rejects_injected_sort_by(client: AsyncClient):
    res = await client.get(
        "/api/v1/media/discover",
        params={"type": "movie", "sort_by": "popularity.desc&with_companies=1"},
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_discover_accepts_known_sort_by(client: AsyncClient):
    res = await client.get(
        "/api/v1/media/discover", params={"type": "movie", "sort_by": "vote_average.desc"}
    )
    assert res.status_code == 200


# ── LOW: login does not leak account existence through timing ───────────────


@pytest.mark.asyncio
async def test_login_hashes_on_unknown_user(client: AsyncClient, monkeypatch):
    """The miss path must still run bcrypt, or response time reveals the answer."""
    calls = []
    real_verify = security.verify_password

    def counting_verify(plain, hashed):
        calls.append(hashed)
        return real_verify(plain, hashed)

    import app.api.endpoints.auth as auth_module

    monkeypatch.setattr(auth_module.security, "verify_password", counting_verify)

    res = await client.post(
        "/api/v1/auth/login",
        data={"username": "nobody@example.com", "password": "AnyPassword1!"},
    )
    assert res.status_code == 400
    assert len(calls) == 1, "expected a dummy hash comparison on the miss path"
