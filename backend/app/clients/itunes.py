import httpx
from typing import List, Dict, Any, Optional
from backend.app.logger import get_logger

logger = get_logger()

class ITunesClient:
    def __init__(self, timeout: int = 15):
        self.base_url = "https://itunes.apple.com"
        self.timeout = timeout

    async def search_album_artwork(self, artist: str, album: str) -> List[Dict[str, Any]]:
        """
        Search iTunes API for album cover art.
        Returns a list of dictionaries with artwork url, dimensions, and release details.
        """
        url = f"{self.base_url}/search"
        params = {
            "term": f"{artist} {album}",
            "entity": "album",
            "limit": 5
        }
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                
                results = []
                for item in data.get("results", []):
                    # iTunes provides artwork URLs like .../100x100bb.jpg.
                    # We can replace 100x100bb.jpg with 1000x1000bb.jpg to fetch high resolution.
                    orig_url = item.get("artworkUrl100", "")
                    high_res_url = orig_url
                    if orig_url and "100x100" in orig_url:
                        high_res_url = orig_url.replace("100x100", "1000x1000")
                        
                    results.append({
                        "artist": item.get("artistName", ""),
                        "album": item.get("collectionName", ""),
                        "url": high_res_url,
                        "thumbnail": orig_url,
                        "resolution": "1000x1000" if high_res_url != orig_url else "Unknown",
                        "source": "iTunes",
                        "release_date": item.get("releaseDate", "")
                    })
                return results
        except Exception as e:
            logger.error(f"Failed to search iTunes for '{artist} - {album}': {e}")
            return []
