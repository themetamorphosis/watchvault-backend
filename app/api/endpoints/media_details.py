"""Media details endpoint.

Fetches complete movie/TV details including credits and trailers from TMDB.
"""

from fastapi import APIRouter, Query
import logging

from app.core.config import settings
from app.services.media_service import get_shared_client

router = APIRouter()
logger = logging.getLogger(__name__)
@router.get("/details")
async def get_media_details(
    tmdb_id: int = Query(...),
    type: str = Query("movie", pattern="^(movie|tv|anime)$"),
):
    """Fetch complete details, credits, and trailer for a movie/TV show from TMDB."""
    if not settings.TMDB_API_KEY:
        return {}

    tmdb_type = "tv" if type in ("tv", "anime") else "movie"
    client = get_shared_client()

    url_details = f"https://api.themoviedb.org/3/{tmdb_type}/{tmdb_id}?api_key={settings.TMDB_API_KEY}"
    url_credits = f"https://api.themoviedb.org/3/{tmdb_type}/{tmdb_id}/credits?api_key={settings.TMDB_API_KEY}"
    url_videos = f"https://api.themoviedb.org/3/{tmdb_type}/{tmdb_id}/videos?api_key={settings.TMDB_API_KEY}"

    try:
        r_details = await client.get(url_details)
        if r_details.status_code != 200:
            return {"error": "Failed to fetch details"}
        details = r_details.json()

        r_credits = await client.get(url_credits)
        credits = r_credits.json() if r_credits.status_code == 200 else {"cast": [], "crew": []}

        r_videos = await client.get(url_videos)
        videos = r_videos.json() if r_videos.status_code == 200 else {"results": []}

        crew = credits.get("crew", [])
        directors = [c.get("name") for c in crew if c.get("job") == "Director"]
        writers = [c.get("name") for c in crew if c.get("job") in ("Writer", "Screenplay", "Teleplay", "Author")]
        composers = [c.get("name") for c in crew if c.get("job") in ("Original Music Composer", "Music", "Composer")]

        cast = credits.get("cast", [])
        top_cast = []
        for member in cast[:8]:
            profile_path = member.get("profile_path")
            top_cast.append({
                "name": member.get("name"),
                "character": member.get("character"),
                "profileUrl": f"https://image.tmdb.org/t/p/w185{profile_path}" if profile_path else None
            })

        video_results = videos.get("results", [])
        trailer_key = None
        for video in video_results:
            if video.get("site") == "YouTube" and video.get("type") == "Trailer":
                trailer_key = video.get("key")
                break
        if not trailer_key and video_results:
            for video in video_results:
                if video.get("site") == "YouTube":
                    trailer_key = video.get("key")
                    break

        title = details.get("title") if tmdb_type == "movie" else details.get("name")
        date_str = details.get("release_date") if tmdb_type == "movie" else details.get("first_air_date")
        year = int(date_str[:4]) if date_str and len(date_str) >= 4 else None

        genres = [g.get("name") for g in details.get("genres", [])]
        poster_path = details.get("poster_path")
        backdrop_path = details.get("backdrop_path")

        runtime = details.get("runtime")
        if not runtime and tmdb_type == "tv":
            run_times = details.get("episode_run_time") or []
            runtime = run_times[0] if run_times else None

        return {
            "tmdbId": tmdb_id,
            "title": title,
            "year": year,
            "overview": details.get("overview"),
            "genres": genres,
            "voteAverage": details.get("vote_average"),
            "voteCount": details.get("vote_count"),
            "posterUrl": f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None,
            "backdropUrl": f"https://image.tmdb.org/t/p/w1280{backdrop_path}" if backdrop_path else None,
            "runtime": runtime,
            "directors": directors,
            "writers": writers,
            "composers": composers,
            "cast": top_cast,
            "trailerKey": trailer_key
        }

    except Exception as e:
        logger.error(f"Error fetching details for tmdb_id={tmdb_id}: {e}")
        return {"error": str(e)}
