"""Artist aliases scoped to one operation, never persisted in configuration/DB."""
import asyncio
import re
from contextvars import ContextVar
from functools import wraps

_current = ContextVar("download_artist_aliases", default=None)


def name_key(name: str) -> str:
    return re.sub(r"[\W_]", "", name.casefold()) or name.casefold().strip()


class ArtistAliasContext:
    def __init__(self):
        self.artists: dict[str, dict] = {}
        self.lookups: dict[str, asyncio.Task] = {}
        self.loaded: set[str] = set()
        self.resolutions: dict[str, asyncio.Task] = {}

    def add(self, artist: dict, credited_name: str = ""):
        mbid = artist.get("id")
        if not mbid:
            return
        entry = self.artists.setdefault(mbid, {"names": set(), "search": set()})
        for name in (artist.get("name"), credited_name):
            if name:
                entry["names"].add(name)
                entry["search"].add(name)
        for alias in artist.get("aliases") or []:
            name = alias.get("name")
            if not name:
                continue
            entry["search"].add(name)
            # Search hints, legal names and untyped aliases are useful to find
            # candidates, but aren't enough to validate an audio identity.
            if alias.get("type") == "Artist name":
                entry["names"].add(name)
        if "aliases" in artist:
            self.loaded.add(mbid)

    def ids_for(self, name: str) -> set[str]:
        key = name_key(name)
        return {mbid for mbid, data in self.artists.items() if any(name_key(n) == key for n in data["names"])}

    def names_for(self, name: str, search: bool = False) -> list[str]:
        ids = self.ids_for(name)
        if not ids and search:
            ids = {mbid for mbid, data in self.artists.items() if any(name_key(n) == name_key(name) for n in data["search"])}
        if len(ids) != 1:
            return []
        return sorted(self.artists[next(iter(ids))]["search" if search else "names"])

    def same_artist(self, expected: str, actual: str, actual_id: str = None) -> bool:
        expected_ids = self.ids_for(expected)
        if len(expected_ids) != 1:
            return False
        if actual_id:
            return actual_id in expected_ids
        return self.ids_for(actual) == expected_ids

    async def resolve(self, name: str):
        """Resolve aliases on the first empty search, even before metadata exists."""
        if self.names_for(name, search=True):
            return
        key = name_key(name)
        if key not in self.resolutions:
            async def fetch():
                from backend.app.clients.musicbrainz import _mb_get
                escaped = name.replace('\\', '\\\\').replace('"', '\\"')
                try:
                    data = await _mb_get('/artist', {'query': f'artist:"{escaped}" OR alias:"{escaped}"', 'limit': 5, 'fmt': 'json'})
                    matches = [artist for artist in (data or {}).get('artists', []) if key in {
                        name_key(n) for n in [artist.get('name') or '', *[a.get('name') or '' for a in artist.get('aliases') or []]]
                    }]
                    if len({a.get('id') for a in matches}) == 1 and matches:
                        await self.ingest({'artist-credit': [{'artist': matches[0]}]})
                except Exception:
                    pass
            self.resolutions[key] = asyncio.create_task(fetch())
        await asyncio.shield(self.resolutions[key])

    async def ingest(self, payload):
        """Harvest existing credits; fetch missing aliases once per artist MBID."""
        artists = []

        def visit(value):
            if isinstance(value, list):
                for item in value:
                    visit(item)
            elif isinstance(value, dict):
                for credit in value.get("artist-credit") or []:
                    if isinstance(credit, dict) and isinstance(credit.get("artist"), dict):
                        artist = credit["artist"]
                        self.add(artist, credit.get("name") or "")
                        artists.append(artist)
                for child in value.values():
                    if isinstance(child, (dict, list)):
                        visit(child)
        visit(payload)
        for artist in artists:
            mbid = artist.get("id")
            if not mbid:
                continue
            if mbid not in self.loaded and mbid not in self.lookups:
                async def fetch(identity=mbid):
                    from backend.app.clients.musicbrainz import _mb_get
                    try:
                        result = await _mb_get(f"/artist/{identity}", {"inc": "aliases", "fmt": "json"})
                        if result:
                            self.add(result)
                    except Exception:
                        # Optional aliases must not abort a download on outage.
                        pass
                self.lookups[mbid] = asyncio.create_task(fetch())
            if mbid in self.lookups:
                await asyncio.shield(self.lookups[mbid])
            # Do not leak alias lists into long-lived album/release caches.
            artist.pop("aliases", None)


def current_artist_aliases():
    return _current.get()


async def search_queries_with_aliases(artist: str, initial_queries: list, title: str = '', album: str = '', album_search: bool = False):
    """A resumed generator means the caller found no usable results; expand then."""
    if not initial_queries:
        return
    seen = {initial_queries[0]}
    yield initial_queries[0]
    context = current_artist_aliases()
    if context:
        await context.resolve(artist)
        for alias in context.names_for(artist, search=True)[:8]:
            if name_key(alias) == name_key(artist):
                continue
            text = re.sub(r'[^\w\s*]', ' ', f'{album if album_search else title} {alias}')
            text = re.sub(r'\s+', ' ', text).strip()
            query = (text, True) if album_search else text
            if text and query not in seen:
                seen.add(query)
                yield query
    for query in initial_queries[1:]:
        if query not in seen:
            seen.add(query)
            yield query


def artist_alias_scope(function):
    @wraps(function)
    async def scoped(*args, **kwargs):
        if _current.get() is not None:
            return await function(*args, **kwargs)
        token = _current.set(ArtistAliasContext())
        try:
            return await function(*args, **kwargs)
        finally:
            _current.reset(token)
    return scoped
