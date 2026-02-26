import asyncio
from app.db.database import AsyncSessionLocal
from app.db.models import MediaCache, WatchlistItem
from app.api.endpoints.media import fetch_and_cache_poster
from sqlalchemy import select, update

async def backfill():
    async with AsyncSessionLocal() as db:
        print("Starting backfill process...")
        
        # 1. Update MediaCache entries with missing descriptions
        q = select(MediaCache).filter((MediaCache.description == None) | (MediaCache.description == ""))
        result = await db.execute(q)
        caches = result.scalars().all()
        
        for c in caches:
            print(f"Fetching cache metadata for '{c.title}' ({c.mediaType})...")
            res = await fetch_and_cache_poster(db, c.title, c.mediaType, c.year)
            desc = res.get("description")
            if desc:
                print(f"  -> Got description: {desc[:50]}...")
                stmt = update(WatchlistItem).where(
                    WatchlistItem.title == c.title,
                    WatchlistItem.mediaType == c.mediaType
                ).values(description=desc)
                await db.execute(stmt)
                await db.commit()
            else:
                print("  -> Still no description found.")
        
        # 2. Sync all WatchlistItems that have no description
        q2 = select(WatchlistItem).filter((WatchlistItem.description == None) | (WatchlistItem.description == ""))
        res2 = await db.execute(q2)
        items = res2.scalars().all()
        
        for item in items:
            print(f"Syncing user item '{item.title}' ({item.mediaType})...")
            q_cache = select(MediaCache).filter(MediaCache.title == item.title, MediaCache.mediaType == item.mediaType)
            if item.year:
                q_cache = q_cache.filter(MediaCache.year == item.year)
            else:
                q_cache = q_cache.filter(MediaCache.year.is_(None))
            
            c_res = await db.execute(q_cache)
            cache_entry = c_res.scalars().first()
            
            if cache_entry and cache_entry.description:
                item.description = cache_entry.description
                print(f"  -> Synced from existing cache: {item.description[:50]}...")
            else:
                res = await fetch_and_cache_poster(db, item.title, item.mediaType, item.year)
                if res.get("description"):
                    item.description = res.get("description")
                    print(f"  -> Fetched new description: {item.description[:50]}...")
            
            # also sync genres and coverUrl if missing
            if cache_entry:
                if (not item.genres or len(item.genres) == 0) and cache_entry.genres:
                    item.genres = cache_entry.genres
                if not item.coverUrl and cache_entry.coverUrl:
                    item.coverUrl = cache_entry.coverUrl
            
            await db.commit()

        print("Backfill complete.")

if __name__ == "__main__":
    asyncio.run(backfill())
