import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.db import models


async def _make_user(db: AsyncSession, handle: str | None, name: str = "Friend") -> dict:
    user_id = str(uuid.uuid4())
    user = models.User(
        id=user_id,
        name=name,
        email=f"{user_id[:8]}@example.com",
        handle=handle,
        password=security.get_password_hash("TestPass123!"),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return {
        "id": user.id,
        "handle": handle,
        "name": name,
        "headers": {
            "Authorization": f"Bearer {security.create_access_token(subject=user.email)}"
        },
    }


@pytest_asyncio.fixture
async def alice(client: AsyncClient, auth_headers, test_user) -> dict:
    """The fixture user, given a handle."""
    res = await client.patch(
        "/api/v1/auth/me", json={"handle": "alice"}, headers=auth_headers
    )
    assert res.status_code == 200, res.text
    return {"id": test_user.id, "handle": "alice", "headers": auth_headers}


@pytest_asyncio.fixture
async def bob(db: AsyncSession) -> dict:
    return await _make_user(db, "bob", name="Bob")


async def _befriend(client: AsyncClient, a: dict, b: dict) -> None:
    res = await client.post(
        "/api/v1/friends/requests", json={"handle": b["handle"]}, headers=a["headers"]
    )
    assert res.status_code == 201, res.text
    request_id = res.json()["id"]
    res = await client.post(
        f"/api/v1/friends/requests/{request_id}/accept", headers=b["headers"]
    )
    assert res.status_code == 200, res.text


# --- Handles ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_handle(client: AsyncClient, auth_headers):
    res = await client.patch(
        "/api/v1/auth/me", json={"handle": "Noor"}, headers=auth_headers
    )
    assert res.status_code == 200
    assert res.json()["handle"] == "noor"  # normalized to lowercase


@pytest.mark.asyncio
async def test_handle_strips_leading_at(client: AsyncClient, auth_headers):
    res = await client.patch(
        "/api/v1/auth/me", json={"handle": "@noor"}, headers=auth_headers
    )
    assert res.status_code == 200
    assert res.json()["handle"] == "noor"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handle", ["ab", "x" * 31, "has space", "has-hyphen", "Ünicode", "admin"]
)
async def test_invalid_handles_rejected(client: AsyncClient, auth_headers, handle):
    res = await client.patch(
        "/api/v1/auth/me", json={"handle": handle}, headers=auth_headers
    )
    assert res.status_code == 422, f"{handle!r} should be rejected"


@pytest.mark.asyncio
async def test_duplicate_handle_conflicts(client: AsyncClient, alice, db: AsyncSession):
    other = await _make_user(db, None)
    res = await client.patch(
        "/api/v1/auth/me", json={"handle": "alice"}, headers=other["headers"]
    )
    assert res.status_code == 409


# --- Search ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_finds_by_handle_prefix(client: AsyncClient, alice, bob):
    res = await client.get("/api/v1/friends/search?q=bo", headers=alice["headers"])
    assert res.status_code == 200
    results = res.json()["results"]
    assert [r["handle"] for r in results] == ["bob"]
    assert results[0]["relationship"] == "none"
    # A search result must never carry an email address.
    assert "email" not in results[0]


@pytest.mark.asyncio
async def test_search_marks_existing_relationships(client: AsyncClient, alice, bob):
    await client.post(
        "/api/v1/friends/requests", json={"handle": "bob"}, headers=alice["headers"]
    )
    outgoing = (
        await client.get("/api/v1/friends/search?q=bob", headers=alice["headers"])
    ).json()["results"][0]
    assert outgoing["relationship"] == "outgoing"

    incoming = (
        await client.get("/api/v1/friends/search?q=ali", headers=bob["headers"])
    ).json()["results"][0]
    assert incoming["relationship"] == "incoming"


@pytest.mark.asyncio
async def test_search_marks_self(client: AsyncClient, alice):
    res = await client.get("/api/v1/friends/search?q=alice", headers=alice["headers"])
    assert res.json()["results"][0]["relationship"] == "self"


@pytest.mark.asyncio
async def test_search_ignores_users_without_a_handle(
    client: AsyncClient, alice, db: AsyncSession
):
    await _make_user(db, None, name="Anonymous")
    res = await client.get("/api/v1/friends/search?q=an", headers=alice["headers"])
    assert res.json()["results"] == []


@pytest.mark.asyncio
async def test_search_wildcards_are_escaped(client: AsyncClient, alice, bob):
    # "%%" — without escaping this LIKE pattern matches every handle in the
    # table and turns search into a user directory dump.
    res = await client.get("/api/v1/friends/search?q=%25%25", headers=alice["headers"])
    assert res.status_code == 200
    assert res.json()["results"] == []


@pytest.mark.asyncio
async def test_search_requires_auth(client: AsyncClient):
    assert (await client.get("/api/v1/friends/search?q=bob")).status_code == 401


# --- Requests --------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_and_accept(client: AsyncClient, alice, bob):
    res = await client.post(
        "/api/v1/friends/requests", json={"handle": "bob"}, headers=alice["headers"]
    )
    assert res.status_code == 201
    request_id = res.json()["id"]

    # Pending shows on both sides, in opposite lists.
    alice_view = (await client.get("/api/v1/friends", headers=alice["headers"])).json()
    assert len(alice_view["outgoing"]) == 1
    assert alice_view["friends"] == []

    bob_view = (await client.get("/api/v1/friends", headers=bob["headers"])).json()
    assert len(bob_view["incoming"]) == 1
    assert bob_view["incoming"][0]["user"]["handle"] == "alice"

    res = await client.post(
        f"/api/v1/friends/requests/{request_id}/accept", headers=bob["headers"]
    )
    assert res.status_code == 200

    for who in (alice, bob):
        view = (await client.get("/api/v1/friends", headers=who["headers"])).json()
        assert len(view["friends"]) == 1
        assert view["incoming"] == [] and view["outgoing"] == []


@pytest.mark.asyncio
async def test_cannot_add_yourself(client: AsyncClient, alice):
    res = await client.post(
        "/api/v1/friends/requests", json={"handle": "alice"}, headers=alice["headers"]
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_request_to_unknown_handle_404s(client: AsyncClient, alice):
    res = await client.post(
        "/api/v1/friends/requests", json={"handle": "ghost"}, headers=alice["headers"]
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_duplicate_request_conflicts(client: AsyncClient, alice, bob):
    await client.post(
        "/api/v1/friends/requests", json={"handle": "bob"}, headers=alice["headers"]
    )
    res = await client.post(
        "/api/v1/friends/requests", json={"handle": "bob"}, headers=alice["headers"]
    )
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_crossing_requests_become_a_friendship(client: AsyncClient, alice, bob):
    # Both sides asking is mutual consent; it must not deadlock on the unique
    # constraint.
    await client.post(
        "/api/v1/friends/requests", json={"handle": "bob"}, headers=alice["headers"]
    )
    res = await client.post(
        "/api/v1/friends/requests", json={"handle": "alice"}, headers=bob["headers"]
    )
    assert res.status_code == 201

    view = (await client.get("/api/v1/friends", headers=alice["headers"])).json()
    assert len(view["friends"]) == 1
    assert view["outgoing"] == []


@pytest.mark.asyncio
async def test_requester_cannot_accept_own_request(client: AsyncClient, alice, bob):
    res = await client.post(
        "/api/v1/friends/requests", json={"handle": "bob"}, headers=alice["headers"]
    )
    request_id = res.json()["id"]
    res = await client.post(
        f"/api/v1/friends/requests/{request_id}/accept", headers=alice["headers"]
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_third_party_cannot_touch_a_request(
    client: AsyncClient, alice, bob, db: AsyncSession
):
    mallory = await _make_user(db, "mallory")
    res = await client.post(
        "/api/v1/friends/requests", json={"handle": "bob"}, headers=alice["headers"]
    )
    request_id = res.json()["id"]

    assert (
        await client.post(
            f"/api/v1/friends/requests/{request_id}/accept", headers=mallory["headers"]
        )
    ).status_code == 404
    assert (
        await client.delete(
            f"/api/v1/friends/requests/{request_id}", headers=mallory["headers"]
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_decline_removes_the_request(client: AsyncClient, alice, bob):
    res = await client.post(
        "/api/v1/friends/requests", json={"handle": "bob"}, headers=alice["headers"]
    )
    request_id = res.json()["id"]
    assert (
        await client.delete(
            f"/api/v1/friends/requests/{request_id}", headers=bob["headers"]
        )
    ).status_code == 200

    # The sender simply sees it gone — never that it was refused.
    view = (await client.get("/api/v1/friends", headers=alice["headers"])).json()
    assert view["outgoing"] == [] and view["friends"] == []


@pytest.mark.asyncio
async def test_sender_can_cancel_their_request(client: AsyncClient, alice, bob):
    res = await client.post(
        "/api/v1/friends/requests", json={"handle": "bob"}, headers=alice["headers"]
    )
    request_id = res.json()["id"]
    assert (
        await client.delete(
            f"/api/v1/friends/requests/{request_id}", headers=alice["headers"]
        )
    ).status_code == 200


@pytest.mark.asyncio
async def test_unfriend(client: AsyncClient, alice, bob):
    await _befriend(client, alice, bob)
    assert (
        await client.delete(f"/api/v1/friends/{bob['id']}", headers=alice["headers"])
    ).status_code == 200

    view = (await client.get("/api/v1/friends", headers=bob["headers"])).json()
    assert view["friends"] == []


# --- Messages --------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_and_read_a_message(client: AsyncClient, alice, bob):
    await _befriend(client, alice, bob)

    res = await client.post(
        f"/api/v1/messages/{bob['id']}",
        json={"body": "  have you seen dune 2  "},
        headers=alice["headers"],
    )
    assert res.status_code == 201
    assert res.json()["body"] == "have you seen dune 2"  # trimmed

    page = (
        await client.get(f"/api/v1/messages/{alice['id']}", headers=bob["headers"])
    ).json()
    assert [m["body"] for m in page["messages"]] == ["have you seen dune 2"]


@pytest.mark.asyncio
async def test_message_with_attachment(client: AsyncClient, alice, bob):
    await _befriend(client, alice, bob)

    res = await client.post(
        f"/api/v1/messages/{bob['id']}",
        json={
            "body": "watch this",
            "attachment": {
                "title": "Dune Part Two",
                "mediaType": "movie",
                "year": 2024,
                "coverUrl": "https://image.tmdb.org/t/p/w500/x.jpg",
            },
        },
        headers=alice["headers"],
    )
    assert res.status_code == 201
    assert res.json()["attachment"]["title"] == "Dune Part Two"


@pytest.mark.asyncio
async def test_attachment_only_message_is_allowed(client: AsyncClient, alice, bob):
    await _befriend(client, alice, bob)
    res = await client.post(
        f"/api/v1/messages/{bob['id']}",
        json={"attachment": {"title": "Arrival", "mediaType": "movie"}},
        headers=alice["headers"],
    )
    assert res.status_code == 201
    assert res.json()["body"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"body": ""}, {"body": "   "}])
async def test_empty_message_rejected(client: AsyncClient, alice, bob, payload):
    await _befriend(client, alice, bob)
    res = await client.post(
        f"/api/v1/messages/{bob['id']}", json=payload, headers=alice["headers"]
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_cannot_message_a_non_friend(client: AsyncClient, alice, bob):
    res = await client.post(
        f"/api/v1/messages/{bob['id']}", json={"body": "hi"}, headers=alice["headers"]
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_pending_request_is_not_a_conversation(client: AsyncClient, alice, bob):
    await client.post(
        "/api/v1/friends/requests", json={"handle": "bob"}, headers=alice["headers"]
    )
    # A request is not consent to be messaged.
    assert (
        await client.post(
            f"/api/v1/messages/{bob['id']}", json={"body": "hi"}, headers=alice["headers"]
        )
    ).status_code == 404
    assert (
        await client.get(f"/api/v1/messages/{bob['id']}", headers=alice["headers"])
    ).status_code == 404


@pytest.mark.asyncio
async def test_unfriending_closes_the_conversation(client: AsyncClient, alice, bob):
    await _befriend(client, alice, bob)
    await client.post(
        f"/api/v1/messages/{bob['id']}", json={"body": "hi"}, headers=alice["headers"]
    )
    await client.delete(f"/api/v1/friends/{bob['id']}", headers=alice["headers"])

    assert (
        await client.get(f"/api/v1/messages/{bob['id']}", headers=alice["headers"])
    ).status_code == 404
    assert (
        await client.get(f"/api/v1/messages/{alice['id']}", headers=bob["headers"])
    ).status_code == 404


@pytest.mark.asyncio
async def test_third_party_cannot_read_a_conversation(
    client: AsyncClient, alice, bob, db: AsyncSession
):
    mallory = await _make_user(db, "mallory")
    await _befriend(client, alice, bob)
    await client.post(
        f"/api/v1/messages/{bob['id']}", json={"body": "secret"}, headers=alice["headers"]
    )

    assert (
        await client.get(f"/api/v1/messages/{alice['id']}", headers=mallory["headers"])
    ).status_code == 404


@pytest.mark.asyncio
async def test_since_cursor_returns_only_new_messages(client: AsyncClient, alice, bob):
    await _befriend(client, alice, bob)
    first = (
        await client.post(
            f"/api/v1/messages/{bob['id']}", json={"body": "one"}, headers=alice["headers"]
        )
    ).json()
    await client.post(
        f"/api/v1/messages/{bob['id']}", json={"body": "two"}, headers=alice["headers"]
    )

    page = (
        await client.get(
            f"/api/v1/messages/{alice['id']}?since={first['id']}", headers=bob["headers"]
        )
    ).json()
    assert [m["body"] for m in page["messages"]] == ["two"]


@pytest.mark.asyncio
async def test_conversation_is_returned_oldest_first(client: AsyncClient, alice, bob):
    await _befriend(client, alice, bob)
    for body in ["one", "two", "three"]:
        await client.post(
            f"/api/v1/messages/{bob['id']}",
            json={"body": body},
            headers=alice["headers"],
        )

    page = (
        await client.get(f"/api/v1/messages/{alice['id']}", headers=bob["headers"])
    ).json()
    assert [m["body"] for m in page["messages"]] == ["one", "two", "three"]


@pytest.mark.asyncio
async def test_conversation_pagination(client: AsyncClient, alice, bob):
    await _befriend(client, alice, bob)
    for i in range(5):
        await client.post(
            f"/api/v1/messages/{bob['id']}",
            json={"body": f"m{i}"},
            headers=alice["headers"],
        )

    page = (
        await client.get(f"/api/v1/messages/{alice['id']}?limit=2", headers=bob["headers"])
    ).json()
    # The tail of the conversation, still oldest-first within the page.
    assert [m["body"] for m in page["messages"]] == ["m3", "m4"]
    assert page["has_more"] is True

    older = (
        await client.get(
            f"/api/v1/messages/{alice['id']}?limit=2&before={page['messages'][0]['id']}",
            headers=bob["headers"],
        )
    ).json()
    assert [m["body"] for m in older["messages"]] == ["m1", "m2"]


@pytest.mark.asyncio
async def test_unread_count_and_marking_read(client: AsyncClient, alice, bob):
    await _befriend(client, alice, bob)
    for body in ["one", "two"]:
        await client.post(
            f"/api/v1/messages/{bob['id']}",
            json={"body": body},
            headers=alice["headers"],
        )

    assert (
        await client.get("/api/v1/messages/unread", headers=bob["headers"])
    ).json()["unread"] == 2
    # The sender's own messages never count as unread for the sender.
    assert (
        await client.get("/api/v1/messages/unread", headers=alice["headers"])
    ).json()["unread"] == 0

    res = await client.post(f"/api/v1/messages/{alice['id']}/read", headers=bob["headers"])
    assert res.json()["marked"] == 2
    assert (
        await client.get("/api/v1/messages/unread", headers=bob["headers"])
    ).json()["unread"] == 0


@pytest.mark.asyncio
async def test_friends_list_carries_unread_counts(client: AsyncClient, alice, bob):
    await _befriend(client, alice, bob)
    await client.post(
        f"/api/v1/messages/{bob['id']}", json={"body": "hi"}, headers=alice["headers"]
    )

    view = (await client.get("/api/v1/friends", headers=bob["headers"])).json()
    assert view["friends"][0]["unread"] == 1


@pytest.mark.asyncio
async def test_conversation_list_orders_by_recency(
    client: AsyncClient, alice, bob, db: AsyncSession
):
    carol = await _make_user(db, "carol")
    await _befriend(client, alice, bob)
    await _befriend(client, alice, carol)

    await client.post(
        f"/api/v1/messages/{bob['id']}", json={"body": "to bob"}, headers=alice["headers"]
    )
    await client.post(
        f"/api/v1/messages/{carol['id']}",
        json={"body": "to carol"},
        headers=alice["headers"],
    )

    convos = (await client.get("/api/v1/messages", headers=alice["headers"])).json()[
        "conversations"
    ]
    assert [c["user"]["handle"] for c in convos] == ["carol", "bob"]
    assert convos[0]["lastMessage"]["body"] == "to carol"


@pytest.mark.asyncio
async def test_messages_survive_the_sender_deleting_their_item(
    client: AsyncClient, alice, bob
):
    """The attachment is a snapshot, so a recommendation can't be rewritten
    later by the sender tidying their own library."""
    await _befriend(client, alice, bob)
    item = (
        await client.post(
            "/api/v1/watchlist",
            json={"title": "Arrival", "mediaType": "movie", "status": "watched"},
            headers=alice["headers"],
        )
    ).json()
    await client.post(
        f"/api/v1/messages/{bob['id']}",
        json={
            "attachment": {"title": "Arrival", "mediaType": "movie", "year": 2016},
        },
        headers=alice["headers"],
    )
    await client.delete(f"/api/v1/watchlist/{item['id']}", headers=alice["headers"])

    page = (
        await client.get(f"/api/v1/messages/{alice['id']}", headers=bob["headers"])
    ).json()
    assert page["messages"][0]["attachment"]["title"] == "Arrival"


@pytest.mark.asyncio
async def test_social_endpoints_require_auth(client: AsyncClient):
    assert (await client.get("/api/v1/friends")).status_code == 401
    assert (await client.get("/api/v1/messages")).status_code == 401
    assert (await client.get("/api/v1/messages/unread")).status_code == 401
