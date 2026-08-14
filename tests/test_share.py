import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.db import models


async def _create_item(client: AsyncClient, auth_headers: dict, **overrides) -> dict:
    payload = {
        "title": f"Shared Title {uuid.uuid4().hex[:6]}",
        "mediaType": "movie",
        "status": "watched",
        **overrides,
    }
    res = await client.post("/api/v1/watchlist", json=payload, headers=auth_headers)
    assert res.status_code == 200, res.text
    return res.json()


async def _create_link(client: AsyncClient, auth_headers: dict, **overrides) -> dict:
    payload = {"slug": f"handle-{uuid.uuid4().hex[:8]}", **overrides}
    res = await client.post("/api/v1/share", json=payload, headers=auth_headers)
    assert res.status_code == 201, res.text
    return res.json()


# --- Link management -------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_list_share_link(client: AsyncClient, auth_headers):
    link = await _create_link(client, auth_headers, label="My picks")
    assert link["label"] == "My picks"
    assert link["statuses"] == []
    assert link["favoritesOnly"] is False

    res = await client.get("/api/v1/share", headers=auth_headers)
    assert res.status_code == 200
    assert [l["id"] for l in res.json()["links"]] == [link["id"]]


@pytest.mark.asyncio
async def test_create_share_link_requires_auth(client: AsyncClient):
    res = await client.post("/api/v1/share", json={"slug": "no-auth-handle"})
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_slug_is_normalized_to_lowercase(client: AsyncClient, auth_headers):
    link = await _create_link(client, auth_headers, slug="MixedCase")
    assert link["slug"] == "mixedcase"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "slug",
    [
        "ab",                 # too short
        "x" * 33,             # too long
        "-leading",           # leading hyphen
        "trailing-",          # trailing hyphen
        "has space",          # space
        "has/slash",          # would change what URL it means
        "under_score",        # underscore not allowed
        "login",              # reserved
    ],
)
async def test_invalid_slugs_rejected(client: AsyncClient, auth_headers, slug):
    res = await client.post("/api/v1/share", json={"slug": slug}, headers=auth_headers)
    assert res.status_code == 422, f"{slug!r} should be rejected"


@pytest.mark.asyncio
async def test_duplicate_slug_conflicts(client: AsyncClient, auth_headers):
    link = await _create_link(client, auth_headers)
    res = await client.post(
        "/api/v1/share", json={"slug": link["slug"]}, headers=auth_headers
    )
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_duplicate_slug_across_users_conflicts(
    client: AsyncClient, auth_headers, db: AsyncSession
):
    link = await _create_link(client, auth_headers)

    other_id = str(uuid.uuid4())
    other = models.User(
        id=other_id,
        name="Other",
        email=f"other-{other_id[:8]}@example.com",
        password=security.get_password_hash("TestPass123!"),
    )
    db.add(other)
    await db.commit()
    other_headers = {
        "Authorization": f"Bearer {security.create_access_token(subject=other.email)}"
    }

    res = await client.post(
        "/api/v1/share", json={"slug": link["slug"]}, headers=other_headers
    )
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_patch_only_changes_supplied_fields(client: AsyncClient, auth_headers):
    link = await _create_link(client, auth_headers, statuses=["wishlist"])
    res = await client.patch(
        f"/api/v1/share/{link['id']}", json={"label": "Renamed"}, headers=auth_headers
    )
    assert res.status_code == 200
    body = res.json()
    assert body["label"] == "Renamed"
    assert body["statuses"] == ["wishlist"]  # untouched by the rename


@pytest.mark.asyncio
async def test_cannot_modify_another_users_link(
    client: AsyncClient, auth_headers, db: AsyncSession
):
    link = await _create_link(client, auth_headers)

    other_id = str(uuid.uuid4())
    other = models.User(
        id=other_id,
        email=f"other-{other_id[:8]}@example.com",
        password=security.get_password_hash("TestPass123!"),
    )
    db.add(other)
    await db.commit()
    other_headers = {
        "Authorization": f"Bearer {security.create_access_token(subject=other.email)}"
    }

    assert (
        await client.patch(
            f"/api/v1/share/{link['id']}",
            json={"label": "Hijacked"},
            headers=other_headers,
        )
    ).status_code == 404
    assert (
        await client.delete(f"/api/v1/share/{link['id']}", headers=other_headers)
    ).status_code == 404


@pytest.mark.asyncio
async def test_delete_revokes_public_access(client: AsyncClient, auth_headers):
    link = await _create_link(client, auth_headers)
    assert (
        await client.get(f"/api/v1/public/watchlist/{link['slug']}")
    ).status_code == 200

    res = await client.delete(f"/api/v1/share/{link['id']}", headers=auth_headers)
    assert res.status_code == 200
    assert (
        await client.get(f"/api/v1/public/watchlist/{link['slug']}")
    ).status_code == 404


# --- Public endpoint -------------------------------------------------------


@pytest.mark.asyncio
async def test_public_watchlist_needs_no_auth(client: AsyncClient, auth_headers):
    await _create_item(client, auth_headers, title="Public Movie")
    link = await _create_link(client, auth_headers, label="Everything")

    # No headers at all — this is the whole point of the feature.
    res = await client.get(f"/api/v1/public/watchlist/{link['slug']}")
    assert res.status_code == 200
    body = res.json()
    assert body["label"] == "Everything"
    assert body["owner"]["name"] == "Test User"
    assert [i["title"] for i in body["items"]] == ["Public Movie"]


@pytest.mark.asyncio
async def test_public_payload_omits_notes_and_user_id(client: AsyncClient, auth_headers):
    await _create_item(client, auth_headers, notes="private thought", title="Noted")
    link = await _create_link(client, auth_headers)

    body = (await client.get(f"/api/v1/public/watchlist/{link['slug']}")).json()
    item = body["items"][0]
    assert "notes" not in item
    assert "userId" not in item
    assert "email" not in body["owner"]


@pytest.mark.asyncio
async def test_public_watchlist_unknown_slug_404(client: AsyncClient):
    res = await client.get("/api/v1/public/watchlist/nobody-here")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_public_watchlist_slug_lookup_is_case_insensitive(
    client: AsyncClient, auth_headers
):
    link = await _create_link(client, auth_headers, slug="casing-test")
    res = await client.get("/api/v1/public/watchlist/Casing-Test")
    assert res.status_code == 200
    assert res.json()["slug"] == "casing-test"


@pytest.mark.asyncio
async def test_status_filter_scopes_items(client: AsyncClient, auth_headers):
    await _create_item(client, auth_headers, title="Seen It", status="watched")
    await _create_item(client, auth_headers, title="Want It", status="wishlist")
    link = await _create_link(client, auth_headers, statuses=["wishlist"])

    body = (await client.get(f"/api/v1/public/watchlist/{link['slug']}")).json()
    assert [i["title"] for i in body["items"]] == ["Want It"]
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_media_type_filter_scopes_items(client: AsyncClient, auth_headers):
    await _create_item(client, auth_headers, title="A Movie", mediaType="movie")
    await _create_item(client, auth_headers, title="An Anime", mediaType="anime")
    link = await _create_link(client, auth_headers, mediaTypes=["anime"])

    body = (await client.get(f"/api/v1/public/watchlist/{link['slug']}")).json()
    assert [i["title"] for i in body["items"]] == ["An Anime"]


@pytest.mark.asyncio
async def test_favorites_only_filter(client: AsyncClient, auth_headers):
    await _create_item(client, auth_headers, title="Plain One")
    fav = await _create_item(client, auth_headers, title="Beloved")
    await client.patch(
        f"/api/v1/watchlist/{fav['id']}/toggle-favorite", headers=auth_headers
    )
    link = await _create_link(client, auth_headers, favoritesOnly=True)

    body = (await client.get(f"/api/v1/public/watchlist/{link['slug']}")).json()
    assert [i["title"] for i in body["items"]] == ["Beloved"]


@pytest.mark.asyncio
async def test_empty_filters_expose_everything(client: AsyncClient, auth_headers):
    await _create_item(client, auth_headers, status="watched")
    await _create_item(client, auth_headers, status="wishlist", mediaType="tv")
    link = await _create_link(client, auth_headers)

    body = (await client.get(f"/api/v1/public/watchlist/{link['slug']}")).json()
    assert body["total"] == 2


@pytest.mark.asyncio
async def test_public_watchlist_pagination(client: AsyncClient, auth_headers):
    for i in range(3):
        await _create_item(client, auth_headers, title=f"Paged {i}")
    link = await _create_link(client, auth_headers)

    first = (
        await client.get(f"/api/v1/public/watchlist/{link['slug']}?limit=2&offset=0")
    ).json()
    assert len(first["items"]) == 2
    assert first["total"] == 3
    assert first["has_more"] is True

    second = (
        await client.get(f"/api/v1/public/watchlist/{link['slug']}?limit=2&offset=2")
    ).json()
    assert len(second["items"]) == 1
    assert second["has_more"] is False


@pytest.mark.asyncio
async def test_public_watchlist_only_shows_owner_items(
    client: AsyncClient, auth_headers, db: AsyncSession
):
    await _create_item(client, auth_headers, title="Mine")
    link = await _create_link(client, auth_headers)

    other_id = str(uuid.uuid4())
    other = models.User(
        id=other_id,
        email=f"other-{other_id[:8]}@example.com",
        password=security.get_password_hash("TestPass123!"),
    )
    db.add(other)
    db.add(
        models.WatchlistItem(
            id=str(uuid.uuid4()),
            userId=other_id,
            title="Theirs",
            mediaType="movie",
            status="watched",
        )
    )
    await db.commit()

    body = (await client.get(f"/api/v1/public/watchlist/{link['slug']}")).json()
    assert [i["title"] for i in body["items"]] == ["Mine"]


@pytest.mark.asyncio
async def test_share_links_cascade_on_user_delete(
    client: AsyncClient, auth_headers, test_user, db: AsyncSession
):
    link = await _create_link(client, auth_headers)
    await db.delete(test_user)
    await db.commit()

    assert (
        await client.get(f"/api/v1/public/watchlist/{link['slug']}")
    ).status_code == 404
