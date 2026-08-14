from fastapi import APIRouter
from app.api.endpoints import (
    auth,
    media_search,
    media_details,
    public,
    share,
    upload,
    watchlist,
    snapshots,
)

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(media_search.router, prefix="/media", tags=["media-search"])
api_router.include_router(media_details.router, prefix="/media", tags=["media-details"])
api_router.include_router(upload.router, prefix="/upload", tags=["upload"])
api_router.include_router(watchlist.router, prefix="/watchlist", tags=["watchlist"])
api_router.include_router(snapshots.router, prefix="/snapshots", tags=["snapshots"])
api_router.include_router(share.router, prefix="/share", tags=["share"])
# Unauthenticated. Kept under its own prefix so no future route lands next to
# an authenticated one by accident.
api_router.include_router(public.router, prefix="/public", tags=["public"])
