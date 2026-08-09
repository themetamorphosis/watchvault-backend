"""Single source of truth for where uploads live and how they're served.

The URL a client receives and the directory the bytes land in are derived from
the same base, so `/uploads/avatars/x.jpg` always resolves to
`<UPLOAD_ROOT>/avatars/x.jpg`.

Previously each endpoint did `settings.UPLOAD_DIR or <its own default>`, so
setting UPLOAD_DIR sent avatars and snapshots to the *same* directory while
their URLs still claimed distinct subpaths.
"""

import os

from app.core.config import settings

URL_PREFIX = "/uploads"

AVATARS_SUBDIR = "avatars"
SNAPSHOTS_SUBDIR = "snapshots"


def upload_root() -> str:
    """Base directory holding every upload subdirectory."""
    return settings.UPLOAD_DIR or os.path.join(os.getcwd(), "uploads")


def get_or_create_root() -> str:
    """Upload root, created if absent. StaticFiles requires it to exist at mount time."""
    root = upload_root()
    os.makedirs(root, exist_ok=True)
    return root


def upload_subdir(name: str) -> str:
    """Return (and create) a subdirectory of the upload root."""
    path = os.path.join(upload_root(), name)
    os.makedirs(path, exist_ok=True)
    return path


def public_url(subdir: str, filename: str) -> str:
    """Public URL for a stored file, matching the StaticFiles mount in main.py."""
    return f"{URL_PREFIX}/{subdir}/{filename}"


def local_path_for_url(url: str | None, subdir: str) -> str | None:
    """Resolve a public upload URL back to a path inside `subdir`, or None.

    Returns None for anything that isn't one of our own upload URLs — external
    avatar URLs, empty values, and any path that escapes the subdirectory. The
    containment check matters because this feeds a delete.
    """
    if not url:
        return None

    prefix = f"{URL_PREFIX}/{subdir}/"
    if not url.startswith(prefix):
        return None

    filename = url[len(prefix):]
    if not filename or "/" in filename or "\\" in filename:
        return None

    root = os.path.realpath(os.path.join(upload_root(), subdir))
    candidate = os.path.realpath(os.path.join(root, filename))

    # Defence in depth: refuse anything that resolves outside the subdirectory.
    if os.path.commonpath([root, candidate]) != root:
        return None

    return candidate
