import json
import logging
import subprocess
from pathlib import Path
from typing import Optional, List, Tuple
import httpx

logger = logging.getLogger(__name__)

class AcoustIDClient:
    def __init__(self):
        self.base_url = "https://api.acoustid.org/v2/lookup"
        self._check_fpcalc()

    def _check_fpcalc(self):
        try:
            result = subprocess.run(["fpcalc", "-version"], capture_output=True, text=True, check=True)
            logger.debug(f"fpcalc found: {result.stdout.strip()}")
        except Exception as e:
            logger.warning(f"fpcalc not found or error: {e}. AcoustID verification will not work.")

    def get_api_key(self) -> Optional[str]:
        from backend.app.main import config_manager
        key = config_manager.config.acoustid.api_key
        return key if key and key.strip() else None

    async def generate_fingerprint(self, file_path: Path) -> Optional[dict]:
        """Runs fpcalc to generate the audio fingerprint."""
        try:
            # We run it in a thread since subprocess is blocking
            import asyncio
            process = await asyncio.create_subprocess_exec(
                "fpcalc", "-json", str(file_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            if process.returncode != 0:
                logger.error(f"fpcalc failed for {file_path}: {stderr.decode()}")
                return None
            return json.loads(stdout.decode())
        except Exception as e:
            logger.error(f"Error generating fingerprint for {file_path}: {e}")
            return None

    async def lookup_fingerprint(self, fingerprint: str, duration: int, meta: str = "recordingids") -> Optional[dict]:
        """Looks up the fingerprint and returns the raw AcoustID response dict."""
        api_key = self.get_api_key()
        if not api_key:
            return None

        params = {
            "client": api_key,
            "meta": meta,
            "duration": int(duration),
            "fingerprint": fingerprint
        }

        try:
            from backend.app.clients.http_client import get_http_client
            client = get_http_client()
            response = await client.post(self.base_url, data=params)
            response.raise_for_status()
            data = response.json()
            if data.get("status") == "ok":
                return data
            else:
                logger.warning(f"AcoustID API error: {data.get('error', 'Unknown')}")
                return None
        except Exception as e:
            logger.error(f"AcoustID lookup failed: {e}")
            return None

    async def lookup_mbids(self, fingerprint: str, duration: int) -> List[str]:
        """Looks up the fingerprint and returns a list of recording MBIDs."""
        data = await self.lookup_fingerprint(fingerprint, duration, "recordings artists releasegroups")
        if not data:
            return []
        
        mbids = set()
        for result in data.get("results", []):
            if result.get("score", 0) >= 0.15:
                for recording in result.get("recordings", []):
                    if "id" in recording:
                        mbids.add(recording["id"])
        return list(mbids)

    def read_musicbrainz_recording_id(self, file_path: Path) -> Optional[str]:
        """Reads MusicBrainz Recording ID from Mutagen tags."""
        try:
            ext = file_path.suffix.lower().strip(".")
            if ext == "mp3":
                from mutagen.id3 import ID3
                tags = ID3(file_path)
                for k, v in tags.items():
                    if k.startswith("UFID:http://musicbrainz.org"):
                        return v.data.decode('utf-8', errors='ignore')
                txxx = tags.getall("TXXX")
                for t in txxx:
                    if t.desc.lower() == "musicbrainz recording id":
                        return t.text[0]
            elif ext == "flac":
                from mutagen.flac import FLAC
                audio = FLAC(file_path)
                mbids = audio.get("musicbrainz_recordingid", [])
                if mbids:
                    return mbids[0]
            elif ext in ["m4a", "mp4"]:
                from mutagen.mp4 import MP4
                audio = MP4(file_path)
                mbids = audio.get("----:com.apple.iTunes:MusicBrainz Recording Id", [])
                if mbids:
                    # Mutagen M4A tags can be bytes
                    val = mbids[0]
                    if isinstance(val, bytes):
                        return val.decode('utf-8', errors='ignore')
                    return str(val)
        except Exception:
            pass
        return None

    async def verify_track_against_metadata(self, file_path: Path) -> Tuple[bool, str]:
        """
        Verifies if the track fingerprint matches the embedded tags.
        Returns (is_valid, reason).
        """
        api_key = self.get_api_key()
        if not api_key:
            return False, "AcoustID API key is not configured"

        # 1. Read metadata from file
        from backend.app.main import read_basic_tags
        try:
            meta = read_basic_tags(file_path)
            tagged_artist = meta.get("artist")
            tagged_title = meta.get("title")
        except Exception as e:
            return False, f"Failed to read file tags: {e}"

        if not tagged_artist or not tagged_title:
            return False, "File is missing basic tags (Artist/Title)"

        tagged_mbid = self.read_musicbrainz_recording_id(file_path)

        # 2. Generate fingerprint
        fp_data = await self.generate_fingerprint(file_path)
        if not fp_data or "fingerprint" not in fp_data or "duration" not in fp_data:
            return False, "Failed to generate audio fingerprint (check if fpcalc is installed)"

        # 3. Lookup with recordings metadata
        data = await self.lookup_fingerprint(fp_data["fingerprint"], fp_data["duration"], "recordings artists releasegroups")
        if not data or not data.get("results"):
            return False, "No match found in AcoustID database"

        results = data.get("results", [])

        # Check if there is any actual non-empty metadata in the results to compare against
        has_any_valid_metadata = False
        for result in results:
            if result.get("score", 0.0) >= 0.15:
                for rec in result.get("recordings", []):
                    if rec.get("title") and rec.get("title").strip():
                        has_any_valid_metadata = True
                        break
                if has_any_valid_metadata:
                    break

        if not has_any_valid_metadata:
            return False, "No match found in AcoustID database"

        # 4. If we have a tagged MBID, check if it matches AcoustID
        if tagged_mbid:
            for result in results:
                if result.get("score", 0.0) >= 0.15:
                    for rec in result.get("recordings", []):
                        if rec.get("id") == tagged_mbid:
                            return True, f"Verified by MusicBrainz Recording ID match ({tagged_mbid})"

        # 5. Fallback: string matching
        import re
        from backend.app.sync import extract_main_artist, clean_search_title, get_artist_aliases
        def norm(s: str) -> str:
            return re.sub(r'[^\w]', '', s).lower()

        clean_tagged_title = norm(clean_search_title(tagged_title))
        norm_tagged_title = norm(tagged_title)
        
        main_art = extract_main_artist(tagged_artist)
        artist_aliases = get_artist_aliases(tagged_artist) + get_artist_aliases(main_art)
        norm_aliases = [norm(a) for a in artist_aliases if a]

        best_match_desc = ""
        highest_score = 0.0

        for result in results:
            score = result.get("score", 0.0)
            if score < 0.15:
                continue

            for rec in result.get("recordings", []):
                rec_title = norm(rec.get("title", ""))
                clean_rec_title = norm(clean_search_title(rec.get("title", "")))

                title_match = (
                    clean_tagged_title in rec_title or 
                    rec_title in clean_tagged_title or
                    norm_tagged_title in rec_title or
                    rec_title in norm_tagged_title or
                    (clean_rec_title and clean_rec_title in clean_tagged_title) or
                    (clean_rec_title and clean_tagged_title in clean_rec_title)
                )

                if title_match:
                    rec_artists = rec.get("artists", [])
                    artist_match = False
                    if not rec_artists:
                        artist_match = True
                    else:
                        for art in rec_artists:
                            rec_art = norm(art.get("name", ""))
                            if any(alias in rec_art or rec_art in alias for alias in norm_aliases if alias):
                                artist_match = True
                                break

                    if artist_match:
                        if score > highest_score:
                            highest_score = score
                            art_name = rec_artists[0].get("name") if rec_artists else "Unknown"
                            best_match_desc = f"Matched AcoustID recording '{rec.get('title')}' by '{art_name}' (score: {score:.2f})"

        if highest_score > 0.0:
            return True, best_match_desc

        # If we got here, it's a mismatch
        top_matches = []
        for result in results[:3]:
            for rec in result.get("recordings", []):
                artists_str = ", ".join(a.get("name", "Unknown") for a in rec.get("artists", []))
                top_matches.append(f"'{rec.get('title')}' by {artists_str}")
        matches_str = " | ".join(top_matches) if top_matches else "No metadata available"
        return False, f"AcoustID matched this audio to: {matches_str} (Expected: '{tagged_artist} - {tagged_title}')"

    async def verify_track(self, file_path: Path, expected_mbid: str) -> bool:
        """
        Returns True if the track matches the expected MBID, or if AcoustID matches the track metadata.
        Returns False ONLY if AcoustID successfully checked and fingerprint belongs to a different track.
        """
        api_key = self.get_api_key()
        if not api_key:
            return True # Opt-out/skip

        fp_data = await self.generate_fingerprint(file_path)
        if not fp_data or "fingerprint" not in fp_data or "duration" not in fp_data:
            logger.warning(f"Could not generate fingerprint for {file_path}. Skipping acoustID check.")
            return True

        mbids = await self.lookup_mbids(fp_data["fingerprint"], fp_data["duration"])
        if not mbids:
            logger.info(f"AcoustID returned no matches for {file_path}. Allowing track.")
            return True

        if expected_mbid in mbids:
            logger.info(f"AcoustID VERIFIED: {file_path} matches expected MBID {expected_mbid}.")
            return True

        # Fallback: check metadata matching (handles album vs single vs remaster MBIDs)
        is_valid, reason = await self.verify_track_against_metadata(file_path)
        if is_valid or "No match found" in reason or "not configured" in reason:
            logger.info(f"AcoustID metadata verification passed for {file_path}: {reason}")
            return True

        logger.warning(f"AcoustID MISMATCH: {file_path} - {reason}")
        return False

acoustid_client = AcoustIDClient()
