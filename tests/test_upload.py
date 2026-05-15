import io
import pytest
from httpx import AsyncClient


def _make_fake_jpeg() -> bytes:
    # JPEG magic bytes + minimal data
    return b"\xff\xd8\xff\xe0" + b"\x00" * 100


def _make_fake_png() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


def _make_fake_text() -> bytes:
    return b"This is not an image file at all."


@pytest.mark.asyncio
async def test_upload_no_auth(client: AsyncClient):
    res = await client.post("/api/v1/upload", files={"file": ("test.jpg", _make_fake_jpeg(), "image/jpeg")})
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_upload_invalid_content_type(client: AsyncClient, auth_headers):
    res = await client.post(
        "/api/v1/upload",
        files={"file": ("test.txt", b"hello", "text/plain")},
        headers=auth_headers,
    )
    assert res.status_code == 400
    assert "Invalid file type" in res.json()["detail"]


@pytest.mark.asyncio
async def test_upload_magic_byte_mismatch(client: AsyncClient, auth_headers):
    # Content-type says image/jpeg but content is plain text
    res = await client.post(
        "/api/v1/upload",
        files={"file": ("fake.jpg", _make_fake_text(), "image/jpeg")},
        headers=auth_headers,
    )
    assert res.status_code == 400
    assert "does not match" in res.json()["detail"]


@pytest.mark.asyncio
async def test_upload_valid_jpeg(client: AsyncClient, auth_headers):
    res = await client.post(
        "/api/v1/upload",
        files={"file": ("photo.jpg", _make_fake_jpeg(), "image/jpeg")},
        headers=auth_headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["imageUrl"].startswith("/uploads/avatars/")


@pytest.mark.asyncio
async def test_upload_valid_png(client: AsyncClient, auth_headers):
    res = await client.post(
        "/api/v1/upload",
        files={"file": ("photo.png", _make_fake_png(), "image/png")},
        headers=auth_headers,
    )
    assert res.status_code == 200
    assert res.json()["imageUrl"].endswith(".png")


@pytest.mark.asyncio
async def test_snapshot_no_auth(client: AsyncClient):
    res = await client.post("/api/v1/snapshots", files={"file": ("snap.jpg", _make_fake_jpeg(), "image/jpeg")})
    assert res.status_code == 401
