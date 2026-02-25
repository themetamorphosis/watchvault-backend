import asyncio
import os
import json
import logging
import httpx
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import create_async_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Parse Postgres connection string appropriately
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://watchvault:watchvault@localhost:5432/watchvault_db")

# For SQLAlchemy, ensure asyncpg is used
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")

TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
TMDB_BASE_URL = "https://api.themoviedb.org/3"

engine = create_async_engine(DATABASE_URL, echo=False)

async def fetch_tmdb_data(client, tmdb_id, media_type):
    url = f"{TMDB_BASE_URL}/{media_type}/{tmdb_id}?api_key={TMDB_API_KEY}"
    response = await client.get(url, timeout=10.0)
    response.raise_for_status()
    return response.json()

async def process_job(client, job):
    job_id, tmdb_id, media_type, attempts = job["id"], job["tmdbId"], job["type"], job["attempts"]
    
    try:
        logger.info(f"Processing job {job_id} for {media_type} {tmdb_id}")
        data = await fetch_tmdb_data(client, tmdb_id, media_type)
        
        # Process TMDB response
        title = data.get("title") if media_type == "movie" else data.get("name")
        poster_path = data.get("poster_path")
        genres = json.dumps([g["name"] for g in data.get("genres", [])])
        runtime = data.get("runtime") if media_type == "movie" else (
            data.get("episode_run_time")[0] if data.get("episode_run_time") else None
        )
        release_date = data.get("release_date") if media_type == "movie" else data.get("first_air_date")
        vote_average = data.get("vote_average")
        
        async with engine.begin() as conn:
            from sqlalchemy import text
            await conn.execute(text("""
                INSERT INTO "Media" ("id", "tmdbId", "type", "title", "posterPath", "genres", "runtime", "releaseDate", "voteAverage", "rawTmdbJson", "lastSyncedAt", "staleAfter", "updatedAt")
                VALUES (gen_random_uuid()::text, :tmdbId, :type, :title, :posterPath, :genres::jsonb, :runtime, :releaseDate, :voteAverage, :rawTmdbJson::jsonb, NOW(), NOW() + INTERVAL '30 days', NOW())
                ON CONFLICT ("tmdbId", "type") DO UPDATE SET
                    "title" = EXCLUDED."title",
                    "posterPath" = EXCLUDED."posterPath",
                    "genres" = EXCLUDED."genres",
                    "runtime" = EXCLUDED."runtime",
                    "releaseDate" = EXCLUDED."releaseDate",
                    "voteAverage" = EXCLUDED."voteAverage",
                    "rawTmdbJson" = EXCLUDED."rawTmdbJson",
                    "lastSyncedAt" = NOW(),
                    "staleAfter" = NOW() + INTERVAL '30 days',
                    "updatedAt" = NOW();
            """), {
                "tmdbId": tmdb_id,
                "type": media_type,
                "title": title or "Unknown",
                "posterPath": poster_path,
                "genres": genres,
                "runtime": runtime,
                "releaseDate": release_date,
                "voteAverage": vote_average,
                "rawTmdbJson": json.dumps(data)
            })
            
            # Mark job as done
            await conn.execute(text("UPDATE \"SyncJob\" SET status='done', \"updatedAt\"=NOW() WHERE id=:id"), {"id": job_id})
        logger.info(f"Successfully processed job {job_id}")
        
    except httpx.HTTPError as e:
        logger.error(f"HTTP error for job {job_id}: {e}")
        # Mark as failed and schedule retry with exponential backoff
        next_run = datetime.now() + timedelta(minutes=2 ** attempts)
        async with engine.begin() as conn:
            from sqlalchemy import text
            await conn.execute(text("""
                UPDATE "SyncJob" 
                SET status='queued', attempts=attempts+1, "runAfter"=:runAfter, "lockedBy"=NULL, "lockedAt"=NULL, "updatedAt"=NOW() 
                WHERE id=:id
            """), {"id": job_id, "runAfter": next_run})
        
    except Exception as e:
        logger.error(f"Unexpected error for job {job_id}: {e}")
        async with engine.begin() as conn:
            from sqlalchemy import text
            await conn.execute(text("""
                UPDATE "SyncJob" 
                SET status='failed', "lockedBy"=NULL, "lockedAt"=NULL, "updatedAt"=NOW() 
                WHERE id=:id
            """), {"id": job_id})

async def run_worker():
    # Setup HTTP client with rate limits (TMDB allows ~50 requests / second, but keeping it safe)
    limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
    
    from sqlalchemy import text
    async with engine.begin() as conn: # To create extension if not exists
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto;"))

    async with httpx.AsyncClient(limits=limits) as client:
        while True:
            try:
                # 1. Start a transaction to pick jobs
                async with engine.begin() as conn:
                    # SELECT ... FOR UPDATE SKIP LOCKED
                    worker_id = str(os.getpid())
                    # Find jobs that are queued and runAfter is reached, OR jobs that are stuck in running for > 5 mins
                    pick_jobs_query = text("""
                        WITH picked AS (
                            SELECT id FROM "SyncJob" 
                            WHERE (status = 'queued' AND "runAfter" <= NOW())
                               OR (status = 'running' AND "lockedAt" < NOW() - INTERVAL '5 minutes')
                            ORDER BY priority DESC, "createdAt" ASC
                            LIMIT 10
                            FOR UPDATE SKIP LOCKED
                        )
                        UPDATE "SyncJob"
                        SET status = 'running', "lockedBy" = :workerId, "lockedAt" = NOW(), "updatedAt" = NOW()
                        FROM picked
                        WHERE "SyncJob".id = picked.id
                        RETURNING "SyncJob".id, "SyncJob"."tmdbId", "SyncJob".type, "SyncJob".attempts;
                    """)
                    
                    result = await conn.execute(pick_jobs_query, {"workerId": worker_id})
                    jobs = result.mappings().fetchall()
                
                if not jobs:
                    await asyncio.sleep(5)
                    continue

                # 2. Process jobs
                tasks = []
                for job in jobs:
                    tasks.append(process_job(client, job))
                
                # Run concurrently
                await asyncio.gather(*tasks)

                # Add slight delay to respect overall rate limiting
                await asyncio.sleep(0.5)

            except Exception as e:
                logger.error(f"Error in worker loop: {e}")
                await asyncio.sleep(5)

if __name__ == "__main__":
    if not TMDB_API_KEY:
        logger.warning("TMDB_API_KEY is not set. Worker will fail fetching TMDB data.")
    logger.info("Starting TMDB Sync Worker...")
    asyncio.run(run_worker())
