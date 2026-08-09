"""Superseded avatars must not accumulate on disk.

Each upload wrote a new timestamped file and rewrote User.image; the previous
file stayed on disk forever and remained publicly reachable.
"""

import os

import pytest

from app.utils.upload_paths import AVATARS_SUBDIR, local_path_for_url, upload_subdir

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _avatar_files() -> set[str]:
    return set(os.listdir(upload_subdir(AVATARS_SUBDIR)))


@pytest.mark.asyncio
async def test_replacing_an_avatar_removes_the_previous_file(
    client, auth_headers, test_user, db
):
    first = await client.post(
        "/api/v1/upload", headers=auth_headers, files={"file": ("a.png", PNG, "image/png")}
    )
    assert first.status_code == 200
    first_url = first.json()["imageUrl"]
    first_path = local_path_for_url(first_url, AVATARS_SUBDIR)
    assert os.path.exists(first_path)

    await db.refresh(test_user)

    second = await client.post(
        "/api/v1/upload", headers=auth_headers, files={"file": ("b.png", PNG, "image/png")}
    )
    assert second.status_code == 200
    second_path = local_path_for_url(second.json()["imageUrl"], AVATARS_SUBDIR)

    assert not os.path.exists(first_path), "superseded avatar was left on disk"
    assert os.path.exists(second_path)


@pytest.mark.asyncio
async def test_upload_succeeds_when_the_previous_file_is_already_gone(
    client, auth_headers, test_user, db
):
    first = await client.post(
        "/api/v1/upload", headers=auth_headers, files={"file": ("a.png", PNG, "image/png")}
    )
    os.remove(local_path_for_url(first.json()["imageUrl"], AVATARS_SUBDIR))
    await db.refresh(test_user)

    second = await client.post(
        "/api/v1/upload", headers=auth_headers, files={"file": ("b.png", PNG, "image/png")}
    )
    assert second.status_code == 200


@pytest.mark.asyncio
async def test_external_avatar_url_is_never_touched(client, auth_headers, test_user, db):
    """A user whose avatar is an external URL must not trip the delete path."""
    test_user.image = "https://lh3.googleusercontent.com/some-avatar"
    await db.commit()

    before = _avatar_files()
    res = await client.post(
        "/api/v1/upload", headers=auth_headers, files={"file": ("a.png", PNG, "image/png")}
    )
    assert res.status_code == 200
    assert len(_avatar_files()) == len(before) + 1


def test_url_resolution_rejects_traversal():
    assert local_path_for_url("/uploads/avatars/../../etc/passwd", AVATARS_SUBDIR) is None
    assert local_path_for_url("/uploads/avatars/sub/dir.png", AVATARS_SUBDIR) is None
    assert local_path_for_url("/uploads/snapshots/x.png", AVATARS_SUBDIR) is None
    assert local_path_for_url("https://example.com/x.png", AVATARS_SUBDIR) is None
    assert local_path_for_url("", AVATARS_SUBDIR) is None
    assert local_path_for_url(None, AVATARS_SUBDIR) is None


def test_url_resolution_accepts_a_normal_avatar():
    resolved = local_path_for_url("/uploads/avatars/user-123.png", AVATARS_SUBDIR)
    assert resolved is not None
    assert resolved.endswith(os.path.join(AVATARS_SUBDIR, "user-123.png"))
