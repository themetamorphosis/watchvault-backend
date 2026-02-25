from fastapi import APIRouter
from app.api.endpoints import auth, media, upload, watchlist, snapshots

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(media.router, prefix="/media", tags=["media"])
api_router.include_router(upload.router, prefix="/upload", tags=["upload"])
api_router.include_router(watchlist.router, prefix="/watchlist", tags=["watchlist"])
api_router.include_router(snapshots.router, prefix="/snapshots", tags=["snapshots"])
