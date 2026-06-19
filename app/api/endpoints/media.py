"""Backward-compatible shim — delegates to media_search and media_details.

This file exists so that watchlist.py and other modules that import
``from app.api.endpoints.media import ...`` continue to work.
"""

from app.api.endpoints.media_search import (  # noqa: F401
    router as search_router,
)
from app.api.endpoints.media_details import (  # noqa: F401
    router as details_router,
)

# Re-exported symbols for backward compatibility
from app.services.media_service import (  # noqa: F401
    get_cached,
    enrich_media_cache,
)
