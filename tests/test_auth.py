import uuid
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_register_success(client: AsyncClient):
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    res = await client.post("/api/v1/auth/register", json={
        "email": email,
        "name": "New User",
        "password": "SecurePass123!",
    })
    assert res.status_code == 200
    data = res.json()
    assert data["email"] == email
    assert data["name"] == "New User"
    assert "id" in data


@pytest.mark.asyncio
async def test_register_duplicate_email(client: AsyncClient, test_user):
    res = await client.post("/api/v1/auth/register", json={
        "email": test_user.email,
        "name": "Dup User",
        "password": "SecurePass123!",
    })
    assert res.status_code == 400
    assert "already exists" in res.json()["detail"]


@pytest.mark.asyncio
async def test_register_weak_password(client: AsyncClient):
    res = await client.post("/api/v1/auth/register", json={
        "email": f"weak-{uuid.uuid4().hex[:8]}@example.com",
        "name": "Weak",
        "password": "short",
    })
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_register_invalid_email(client: AsyncClient):
    res = await client.post("/api/v1/auth/register", json={
        "email": "not-an-email",
        "name": "Bad Email",
        "password": "SecurePass123!",
    })
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient, test_user):
    res = await client.post("/api/v1/auth/login", data={
        "username": test_user.email,
        "password": "TestPass123!",
    })
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient, test_user):
    res = await client.post("/api/v1/auth/login", data={
        "username": test_user.email,
        "password": "WrongPassword123!",
    })
    assert res.status_code == 400
    assert "Incorrect" in res.json()["detail"]


@pytest.mark.asyncio
async def test_login_nonexistent_user(client: AsyncClient):
    res = await client.post("/api/v1/auth/login", data={
        "username": "nobody@example.com",
        "password": "Whatever123!",
    })
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_read_me(client: AsyncClient, test_user, auth_headers):
    res = await client.get("/api/v1/auth/me", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["email"] == test_user.email
    assert data["name"] == test_user.name


@pytest.mark.asyncio
async def test_read_me_no_token(client: AsyncClient):
    res = await client.get("/api/v1/auth/me")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_read_me_invalid_token(client: AsyncClient):
    res = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer invalid.token.here"})
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_update_me_name(client: AsyncClient, test_user, auth_headers):
    res = await client.patch("/api/v1/auth/me", json={"name": "Updated Name"}, headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["name"] == "Updated Name"


@pytest.mark.asyncio
async def test_update_me_password(client: AsyncClient, test_user, auth_headers):
    # A password change now requires proof of the current one; see
    # test_security_hardening.py for the rejection cases.
    res = await client.patch(
        "/api/v1/auth/me",
        json={"password": "NewSecure123!", "current_password": "TestPass123!"},
        headers=auth_headers,
    )
    assert res.status_code == 200

    # Verify new password works
    login_res = await client.post("/api/v1/auth/login", data={
        "username": test_user.email,
        "password": "NewSecure123!",
    })
    assert login_res.status_code == 200
