import os
from fastapi import HTTPException, UploadFile

ALLOWED_TYPES = ["image/jpeg", "image/png", "image/webp", "image/gif"]
ALLOWED_MAGIC = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"RIFF": "image/webp",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
}

_CHUNK_SIZE = 64 * 1024


# Extension to store for each detected format. Keyed by the MIME type that
# ALLOWED_MAGIC maps a signature to.
EXTENSION_BY_MIME = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


def detect_mime(content: bytes) -> str | None:
    """Return the MIME type implied by the file's magic bytes, or None."""
    for sig, mime in ALLOWED_MAGIC.items():
        if content[:len(sig)] == sig:
            return mime
    return None


def validate_magic_bytes(content: bytes) -> bool:
    """Check that file content starts with a known image magic bytes signature."""
    return detect_mime(content) is not None


def extension_for_content(content: bytes, default: str = "jpg") -> str:
    """Extension to store a file under, derived from its actual bytes.

    Never trust the client's filename for this. `sanitize_extension` strips
    non-alphanumerics but happily returns "html" for `evil.html`, and
    StaticFiles picks the response Content-Type from the extension — so a
    GIF-prefixed HTML document was stored and then served as text/html from
    our own origin.
    """
    mime = detect_mime(content)
    if mime is None:
        return default
    return EXTENSION_BY_MIME.get(mime, default)


async def read_upload_capped(file: UploadFile, max_size: int) -> bytes:
    """Read an upload in chunks, aborting as soon as it exceeds max_size.

    Reading the whole body first and checking the length afterwards means a
    client can force the server to buffer arbitrarily large payloads before
    the limit is ever applied. This bails out after at most one chunk past
    the limit.
    """
    chunks: list[bytes] = []
    total = 0

    while True:
        chunk = await file.read(_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_size:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Max {max_size // (1024 * 1024)}MB.",
            )
        chunks.append(chunk)

    return b"".join(chunks)


def validate_upload_file(file: UploadFile, content: bytes, max_size: int) -> None:
    """Validate a file upload against type, magic bytes, and size constraints.

    Raises HTTPException on validation failure.
    """
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="Invalid file type. Use JPEG, PNG, WebP, or GIF.")

    if len(content) > max_size:
        raise HTTPException(status_code=400, detail=f"File too large. Max {max_size // (1024 * 1024)}MB.")

    if not validate_magic_bytes(content):
        raise HTTPException(status_code=400, detail="File content does not match an allowed image format.")


def get_upload_dir(path: str) -> str:
    """Ensure upload directory exists and return its path."""
    os.makedirs(path, exist_ok=True)
    return path


def sanitize_extension(filename: str, default: str = "jpg") -> str:
    """Extract and sanitize the file extension."""
    if filename and "." in filename:
        ext = filename.split(".")[-1]
    else:
        ext = default
    return "".join(c for c in ext if c.isalnum())[:10] or default
