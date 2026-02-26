import asyncio
from app.api.endpoints.media import _fetch_jikan_poster, _fetch_tvmaze_poster

async def test():
    anime = await _fetch_jikan_poster("Naruto")
    print("Anime:", anime)
    tv = await _fetch_tvmaze_poster("Breaking Bad")
    print("TV:", tv)

if __name__ == "__main__":
    asyncio.run(test())
