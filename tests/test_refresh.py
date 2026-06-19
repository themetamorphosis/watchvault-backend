import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models
from app.core import security


@pytest.mark.asyncio
async def test_refresh_issues_new_tokens(client: AsyncClient, test_user):
    # Login to get initial tokens
    login_res = await client.post("/api/v1/auth/login", data={
        "username": test_user.email,
        "password": "TestPass123!",
    })
    assert login_res.status_code == 200
    tokens = login_res.json()
    old_access = tokens["access_token"]
    old_refresh = tokens["refresh_token"]

    # Refresh with the refresh token
    refresh_res = await client.post("/api/v1/auth/refresh", json={
        "refresh_token": old_refresh,
    })
    assert refresh_res.status_code == 200
    new_tokens = refresh_res.json()
    assert "access_token" in new_tokens
    assert "refresh_token" in new_tokens
    # Refresh token must change (new family claim); access may be same if generated same second
    assert new_tokens["refresh_token"] != old_refresh


@pytest.mark.asyncio
async def test_refresh_rejects_used_token(client: AsyncClient, test_user):
    # Login
    login_res = await client.post("/api/v1/auth/login", data={
        "username": test_user.email,
        "password": "TestPass123!",
    })
    refresh_token = login_res.json()["refresh_token"]

    # First refresh — should succeed
    res1 = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert res1.status_code == 200

    # Replay the same refresh token — should fail (family rotated)
    res2 = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert res2.status_code == 401
    assert "revoked" in res2.json()["detail"].lower()


@pytest.mark.asyncio
async def test_refresh_rejects_wrong_family(client: AsyncClient, test_user, db: AsyncSession):
    # Login
    login_res = await client.post("/api/v1/auth/login", data={
        "username": test_user.email,
        "password": "TestPass123!",
    })
    refresh_token = login_res.json()["refresh_token"]

    # Tamper: change the user's token_family in DB
    test_user.token_family = "tampered-family"
    await db.commit()

    res = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_family(client: AsyncClient, test_user, auth_headers):
    # Login to set token_family
    login_res = await client.post("/api/v1/auth/login", data={
        "username": test_user.email,
        "password": "TestPass123!",
    })
    refresh_token = login_res.json()["refresh_token"]

    # Logout
    logout_res = await client.post("/api/v1/auth/logout", headers=auth_headers)
    assert logout_res.status_code == 200
    assert logout_res.json()["ok"] is True

    # Refresh after logout should fail
    refresh_res = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert refresh_res.status_code == 401


@pytest.mark.asyncio
async def test_refresh_missing_token(client: AsyncClient):
    res = await client.post("/api/v1/auth/refresh", json={})
    assert res.status_code == 400
    assert "required" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_refresh_invalid_token(client: AsyncClient):
    res = await client.post("/api/v1/auth/refresh", json={"refresh_token": "invalid.token.here"})
    assert res.status_code == 401
