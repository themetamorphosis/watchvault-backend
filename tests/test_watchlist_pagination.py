"""GET /watchlist pagination.

The endpoint used to default to `limit=0` — the entire library in one response,
held fully in memory on both sides. The machinery was built and tested; it just
wasn't the default.
"""

import pytest

from app.db import models


async def _seed(db, user, count: int):
    for i in range(count):
        db.add(
            models.WatchlistItem(
                id=f"page-item-{i}",
                userId=user.id,
                title=f"Film {i:03d}",
                mediaType="movie",
                status="watched",
            )
        )
    await db.commit()


@pytest.mark.asyncio
async def test_default_limit_is_a_page_not_everything(client, auth_headers, test_user, db):
    await _seed(db, test_user, 120)

    res = await client.get("/api/v1/watchlist", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()

    assert len(body["items"]) == 100
    assert body["limit"] == 100
    assert body["total"] == 120
    assert body["has_more"] is True


@pytest.mark.asyncio
async def test_explicit_zero_still_returns_everything(client, auth_headers, test_user, db):
    """`0` remains the documented opt-out for callers that want the lot."""
    await _seed(db, test_user, 120)

    res = await client.get("/api/v1/watchlist?limit=0", headers=auth_headers)
    body = res.json()

    assert len(body["items"]) == 120
    assert body["has_more"] is False


@pytest.mark.asyncio
async def test_paging_covers_every_row_exactly_once(client, auth_headers, test_user, db):
    await _seed(db, test_user, 120)

    seen: list[str] = []
    offset = 0
    while True:
        res = await client.get(
            f"/api/v1/watchlist?limit=50&offset={offset}", headers=auth_headers
        )
        body = res.json()
        seen.extend(i["id"] for i in body["items"])
        if not body["has_more"]:
            break
        offset += 50

    assert len(seen) == 120
    assert len(set(seen)) == 120


@pytest.mark.asyncio
async def test_has_more_is_false_on_the_final_page(client, auth_headers, test_user, db):
    await _seed(db, test_user, 120)

    res = await client.get(
        "/api/v1/watchlist?limit=100&offset=100", headers=auth_headers
    )
    body = res.json()

    assert len(body["items"]) == 20
    assert body["has_more"] is False


@pytest.mark.asyncio
async def test_small_library_reports_no_further_pages(client, auth_headers, test_user, db):
    await _seed(db, test_user, 3)

    body = (await client.get("/api/v1/watchlist", headers=auth_headers)).json()

    assert len(body["items"]) == 3
    assert body["total"] == 3
    assert body["has_more"] is False
