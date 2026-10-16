import base64
import html as html_lib
import json
import re
from html.parser import HTMLParser
from typing import Any, Dict, List
from urllib.parse import urlparse

from backend.app.clients.http_client import get_http_client


class _EmbedTrackParser(HTMLParser):
    """Extract the server-rendered track headings from Spotify's public embed."""

    def __init__(self):
        super().__init__()
        self.heading = None
        self.buffer = []
        self.headings = []

    def handle_starttag(self, tag, attrs):
        if tag in {"h3", "h4"}:
            self.heading = tag
            self.buffer = []

    def handle_data(self, data):
        if self.heading:
            self.buffer.append(data)

    def handle_endtag(self, tag):
        if tag == self.heading:
            value = re.sub(r"\s+", " ", "".join(self.buffer)).strip()
            if value:
                self.headings.append((tag, value))
            self.heading = None


def _parse_embed_tracks(html: str) -> List[Dict[str, Any]]:
    parser = _EmbedTrackParser()
    parser.feed(html)
    tracks = []
    for index, (tag, title) in enumerate(parser.headings):
        if tag != "h3":
            continue
        artist = next((value for next_tag, value in parser.headings[index + 1:] if next_tag == "h4"), "")
        # Spotify renders the explicit-content badge as a leading "E" in the
        # same heading as the artist, sometimes with no separating whitespace
        # ("EDrake" / "ENicki Minaj"). It is not part of the artist name.
        artist = re.sub(r"^E\s*(?=[A-ZÀ-ÖØ-Þ])", "", artist).strip()
        if artist:
            tracks.append({"artist": artist, "title": title, "album": "", "duration": None})
    return tracks


def _parse_embed_playlist(html: str) -> Dict[str, Any]:
    """Extract playlist name, description, artwork_url, and tracks from Spotify embed HTML."""
    name = ""
    description = ""
    artwork_url = ""
    thumbnail_url = ""
    tracks: List[Dict[str, Any]] = []

    next_data_match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if next_data_match:
        try:
            payload = json.loads(next_data_match.group(1))
            entity = (
                ((payload.get("props") or {}).get("pageProps") or {})
                .get("state", {})
                .get("data", {})
                .get("entity")
                or {}
            )
            name = html_lib.unescape(re.sub(r"\s+", " ", (entity.get("name") or entity.get("title") or "")).strip())
            raw_desc = re.sub(r"\s+", " ", (entity.get("description") or "")).strip()
            if raw_desc:
                description = raw_desc
            sources = ((entity.get("coverArt") or {}).get("sources")) or []
            if isinstance(sources, list) and sources:
                best_source = max(
                    (s for s in sources if isinstance(s, dict) and s.get("url")),
                    key=lambda s: (int(s.get("width") or 0), int(s.get("height") or 0)),
                    default=None,
                )
                if best_source:
                    artwork_url = str(best_source.get("url") or "").strip()
                    # Keep the original for sidecars; use a supplied small
                    # variant for the preview without inventing CDN URLs.
                    suitable = [s for s in sources if isinstance(s, dict) and s.get("url") and int(s.get("width") or 0) >= 250]
                    thumbnail = min(suitable, key=lambda s: int(s.get("width") or 0), default=best_source)
                    thumbnail_url = str(thumbnail.get("url") or "").strip()

            track_list = entity.get("trackList") or []
            if isinstance(track_list, list):
                for item in track_list:
                    if not isinstance(item, dict):
                        continue
                    t_title = re.sub(r"\s+", " ", (item.get("title") or "").replace("\xa0", " ")).strip()
                    t_artist = re.sub(r"\s+", " ", (item.get("subtitle") or "").replace("\xa0", " ")).strip()
                    if t_title and t_artist:
                        tracks.append({
                            "artist": t_artist,
                            "title": t_title,
                            "album": "",
                            "duration": item.get("duration"),
                        })
        except Exception:
            pass

    if not tracks:
        tracks = _parse_embed_tracks(html)

    if not name:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if title_match:
            candidate = re.sub(r"\s+", " ", title_match.group(1)).strip()
            candidate = re.sub(r"\s*[·|-]\s*Spotify$", "", candidate, flags=re.IGNORECASE).strip()
            if candidate:
                name = candidate

    if not name:
        og_title = re.search(
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
            html,
            re.IGNORECASE,
        ) or re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
            html,
            re.IGNORECASE,
        )
        if og_title:
            name = html_lib.unescape(re.sub(r"\s+", " ", og_title.group(1)).strip())

    if not artwork_url:
        image_match = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
            html,
            re.IGNORECASE,
        ) or re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            html,
            re.IGNORECASE,
        )
        if image_match:
            artwork_url = html_lib.unescape(image_match.group(1).strip())

    return {
        "name": name,
        "description": description,
        "artwork_url": artwork_url,
        "thumbnail_url": thumbnail_url or artwork_url,
        "tracks": tracks,
    }


class SpotifyClient:
    """Spotify Web API client for public playlist imports."""

    def __init__(self, client_id: str, client_secret: str, timeout: int = 20):
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.timeout = timeout

    @staticmethod
    def playlist_id(value: str) -> str:
        value = (value or "").strip()
        if re.fullmatch(r"[A-Za-z0-9]{22}", value):
            return value
        parsed = urlparse(value)
        if parsed.netloc.lower() not in {"open.spotify.com", "spotify.com", "www.spotify.com"}:
            raise ValueError("Inserisci un link di playlist Spotify valido.")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2 or parts[0] != "playlist" or not re.fullmatch(r"[A-Za-z0-9]{22}", parts[1]):
            raise ValueError("Il link deve puntare a una playlist Spotify.")
        return parts[1]

    async def _access_token(self) -> str:
        if not self.client_id or not self.client_secret:
            raise ValueError("Spotify client ID e client secret non sono configurati.")
        credentials = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        client = await get_http_client()
        response = await client.post("https://accounts.spotify.com/api/token",
                                     headers={"Authorization": f"Basic {credentials}"},
                                     data={"grant_type": "client_credentials"}, timeout=self.timeout)
        response.raise_for_status()
        token = response.json().get("access_token")
        if not token:
            raise ValueError("Spotify non ha restituito un access token.")
        return token

    async def get_playlist(self, value: str) -> Dict[str, Any]:
        playlist_id = self.playlist_id(value)
        client = await get_http_client()
        embed_response = await client.get(
            f"https://open.spotify.com/embed/playlist/{playlist_id}", timeout=self.timeout)
        embed_response.raise_for_status()
        parsed_embed = _parse_embed_playlist(embed_response.text)
        embed_tracks = parsed_embed["tracks"]
        if embed_tracks:
            title = parsed_embed["name"]
            artwork_url = parsed_embed["artwork_url"]
            description = parsed_embed["description"]

            # oEmbed is the authoritative public source for the playlist's
            # display name and current image.  Embed HTML has changed shape a
            # few times and can expose the owner/page title or a stale image.
            try:
                oembed_resp = await client.get(
                    f"https://open.spotify.com/oembed?url=https://open.spotify.com/playlist/{playlist_id}",
                    timeout=self.timeout,
                )
                if oembed_resp.status_code == 200:
                    oembed_data = oembed_resp.json()
                    oembed_title = html_lib.unescape(
                        re.sub(r"\s+", " ", (oembed_data.get("title") or "")).strip()
                    )
                    oembed_art = html_lib.unescape((oembed_data.get("thumbnail_url") or "").strip())
                    if oembed_title:
                        title = oembed_title
                    if oembed_art:
                        artwork_url = oembed_art
            except Exception:
                pass

            return {
                "id": playlist_id,
                "name": title or "Spotify playlist",
                "description": description,
                "artwork_url": artwork_url,
                "thumbnail_url": (parsed_embed.get("thumbnail_url") if artwork_url == parsed_embed.get("artwork_url") else artwork_url) or artwork_url,
                "tracks": embed_tracks,
            }

        # Optional compatibility fallback for installations that already have API credentials.
        # Public embeds are intentionally attempted first, so credentials are not required.
        token = await self._access_token()
        response = await client.get(
            f"https://api.spotify.com/v1/playlists/{playlist_id}",
            params={"fields": "id,name,description,images,tracks.next,tracks.items(track(id,name,artists(name),album(name),duration_ms))"},
            headers={"Authorization": f"Bearer {token}"}, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        tracks: List[Dict[str, Any]] = []
        page = data.get("tracks", {})
        while True:
            for item in page.get("items", []):
                track = item.get("track") or {}
                artists = track.get("artists") or []
                if track.get("name") and artists:
                    tracks.append({"artist": artists[0].get("name", ""), "title": track["name"],
                                   "album": (track.get("album") or {}).get("name", ""),
                                   "duration": track.get("duration_ms")})
            next_url = page.get("next")
            if not next_url:
                break
            response = await client.get(next_url, headers={"Authorization": f"Bearer {token}"}, timeout=self.timeout)
            response.raise_for_status()
            page = response.json()
        data["tracks"] = tracks
        data["artwork_url"] = ((data.get("images") or [{}])[0].get("url") or "")
        images = [i for i in data.get("images", []) if i.get("url") and int(i.get("width") or 0) >= 250]
        data["thumbnail_url"] = min(images, key=lambda i: int(i.get("width") or 0), default={}).get("url") or data["artwork_url"]
        return data
