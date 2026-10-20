import json
import subprocess
import asyncio
import time
from pathlib import Path
from typing import Optional, List, Tuple
import httpx
from backend.app.logger import get_logger

logger = get_logger()
_lookup_lock = asyncio.Lock()
_last_lookup = 0.0


def verification_status(is_valid: bool, reason: str, stored_status: str = "") -> str:
    """Classify both new results and historical bool/reason database rows."""
    text = (reason or "").lower()
    if stored_status in {"unknown", "unavailable", "skipped", "mismatch"}:
        return stored_status
    if "api unavailable" in text or "unexpected error" in text:
        return "unavailable"
    if "no match found" in text or "insufficient" in text:
        return "unknown"
    if text.startswith("skipped:") or "not configured" in text:
        return "skipped"
    return "verified" if is_valid else "mismatch"

class AcoustIDClient:
    def __init__(self):
        self.base_url = "https://api.acoustid.org/v2/lookup"
        self._fpcalc_checked = False
        self._fpcalc_ok = False
        self._fpcalc_lock = asyncio.Lock()

    def _check_fpcalc(self):
        if self._fpcalc_checked:
            return
        self._fpcalc_checked = True
        try:
            result = subprocess.run(["fpcalc", "-version"], capture_output=True, text=True, check=True, timeout=10)
            logger.debug(f"fpcalc found: {result.stdout.strip()}")
            self._fpcalc_ok = True
        except Exception as e:
            logger.warning(f"fpcalc not found or error: {e}. AcoustID verification will not work.")
            self._fpcalc_ok = False

    def get_api_key(self) -> Optional[str]:
        from backend.app.main import config_manager
        key = config_manager.config.acoustid.api_key if config_manager.config else ""
        return key.strip() if key and key.strip() else None

    async def generate_fingerprint(self, file_path: Path) -> Optional[dict]:
        """Runs fpcalc to generate the audio fingerprint."""
        async with self._fpcalc_lock:
            await asyncio.to_thread(self._check_fpcalc)
        if not self._fpcalc_ok:
            return {"error": "fpcalc is not available; check the installed fingerprint tool"}
        try:
            # Fingerprint generation is bounded and does not block the event loop.
            process = await asyncio.create_subprocess_exec(
                "fpcalc", "-json", str(file_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                if process.returncode is None:
                    process.kill()
                    await process.communicate()
                raise
            if process.returncode != 0:
                detail = " ".join(stderr.decode(errors="replace").split())[:400]
                reason = f"Audio decoder failed (fpcalc exit {process.returncode}): {detail or 'no diagnostic output'}"
                logger.warning("%s: %s. Partial fingerprints are not used.", file_path, reason)
                return {"error": reason}
            data = json.loads(stdout.decode())
            if not isinstance(data.get("fingerprint"), str) or not data["fingerprint"]:
                return {"error": "fpcalc returned an empty or invalid fingerprint"}
            # Some MP3 demuxers estimate duration using the entire file size,
            # including large embedded covers. AcoustID filters by duration,
            # so that estimate can hide an otherwise near-perfect fingerprint.
            def audio_duration():
                from mutagen import File
                audio = File(file_path)
                return float(audio.info.length) if audio is not None and getattr(audio, "info", None) else 0
            try:
                duration = await asyncio.to_thread(audio_duration)
                if duration > 0:
                    original = float(data.get("duration") or 0)
                    if abs(original - duration) > .5:
                        logger.info("Corrected AcoustID duration for %s: fpcalc %.2fs -> audio %.2fs",
                                    file_path.name, original, duration)
                    data["duration"] = duration
            except Exception as exc:
                logger.debug("Audio duration unavailable for %s (%s); using fpcalc duration", file_path.name, type(exc).__name__)
            if float(data.get("duration") or 0) <= 0:
                return {"error": "Audio duration is unavailable; fingerprint lookup was not sent"}
            return data
        except Exception as e:
            logger.error(f"Error generating fingerprint for {file_path}: {e}")
            return {"error": f"Fingerprint generation failed: {type(e).__name__}"}

    async def lookup_fingerprint(self, fingerprint: str, duration: int, meta: str = "recordingids", api_key: Optional[str] = None) -> Optional[dict]:
        """Looks up the fingerprint and returns the raw AcoustID response dict."""
        api_key = api_key.strip() if api_key else self.get_api_key()
        if not api_key:
            return None

        params = {
            "client": api_key,
            "meta": meta,
            "duration": int(duration),
            "fingerprint": fingerprint
        }

        global _last_lookup
        from backend.app.clients.http_client import get_http_client
        client = await get_http_client()
        failure = "Unknown API error"
        for attempt in range(3):
            retryable = True
            delay = 1.0 * (2 ** attempt)
            try:
                # Shared by scans and all downloads: at most two calls/second.
                async with _lookup_lock:
                    await asyncio.sleep(max(0, .5 - (time.monotonic() - _last_lookup)))
                    _last_lookup = time.monotonic()
                    response = await client.post(self.base_url, data=params, timeout=20)
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError("Invalid JSON response shape")
                if data.get("status") == "ok":
                    if not isinstance(data.get("results"), list):
                        raise ValueError("Missing results in successful response")
                    return data
                error = data.get("error") or {}
                failure = str(error.get("message") or "API rejected the lookup") if isinstance(error, dict) else str(error)
                retryable = any(word in failure.lower() for word in ("internal", "temporar", "limit", "timeout", "unavailable"))
            except httpx.HTTPStatusError as exc:
                failure = f"HTTP {exc.response.status_code}"
                retryable = exc.response.status_code in (408, 429) or exc.response.status_code >= 500
                retry_after = exc.response.headers.get("Retry-After", "")
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    pass
            except (httpx.TransportError, ValueError) as exc:
                failure = type(exc).__name__
            if not retryable or attempt == 2 or delay > 30:
                break
            logger.warning("AcoustID lookup unavailable (%s); retry %s/3 in %.1fs", failure, attempt + 2, delay)
            await asyncio.sleep(delay)
        logger.warning("AcoustID lookup unavailable after attempts: %s", failure)
        return {"status": "error", "error": {"message": failure}}

    async def lookup_mbids(self, fingerprint: str, duration: int) -> List[str]:
        """Looks up the fingerprint and returns a list of recording MBIDs."""
        data = await self.lookup_fingerprint(fingerprint, duration, "recordings artists releasegroups")
        if not data or data.get("status") == "error":
            return []
        
        mbids = set()
        for result in data.get("results", []):
            if result.get("score", 0) >= 0.6:
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

    async def verify_track_against_metadata(
        self,
        file_path: Path,
        expected_artist: Optional[str] = None,
        expected_title: Optional[str] = None,
        expected_mbid: Optional[str] = None,
        require_recording_match: bool = False,
    ) -> Tuple[bool, str]:
        """
        Verifies if the track fingerprint matches the expected or embedded metadata.
        Returns (is_valid, reason).
        """
        api_key = self.get_api_key()
        if not api_key:
            return True, "Skipped: AcoustID API key is not configured"

        # 1. Read metadata from file or fallback to expected metadata
        tagged_artist = expected_artist
        tagged_title = expected_title
        # Never trust peer-supplied ID tags over explicitly requested metadata.
        tagged_mbid = expected_mbid

        if not tagged_artist or not tagged_title:
            from backend.app.main import read_basic_tags
            try:
                meta = read_basic_tags(file_path, allow_path_fallback=False)
                tagged_artist = tagged_artist or meta.get("artist")
                tagged_title = tagged_title or meta.get("title")
            except Exception:
                pass

        if not tagged_mbid and expected_artist is None and expected_title is None:
            tagged_mbid = self.read_musicbrainz_recording_id(file_path)

        # 2. Generate fingerprint
        fp_data = await self.generate_fingerprint(file_path)
        if not fp_data or "fingerprint" not in fp_data or "duration" not in fp_data:
            detail = (fp_data or {}).get("error") or "Failed to generate audio fingerprint"
            return True, f"Skipped: {detail}"

        # 3. Lookup with recordings metadata
        data = await self.lookup_fingerprint(fp_data["fingerprint"], fp_data["duration"], "recordings releasegroups")
        if not data or data.get("status") == "error":
            error = (data or {}).get("error") or {}
            message = error.get("message", "No API response") if isinstance(error, dict) else str(error)
            return False, f"AcoustID API unavailable: {message}"
        if not data.get("results"):
            logger.info("AcoustID lookup succeeded with no results for %s (duration %.2fs)", file_path.name, fp_data["duration"])
            return False, (
                f"No match found in AcoustID database (audio duration {fp_data['duration']:.2f}s; "
                f"expected recording {tagged_mbid or 'not available'}). "
                "Audio is not verified; altered speed/pitch or an unregistered fingerprint can cause this."
            )

        results = data.get("results", [])
        logger.info("AcoustID lookup: %s results, best score %.6f, expected recording %s",
                    len(results), max((float(r.get("score") or 0) for r in results), default=0), tagged_mbid or "not available")

        # Check if there is any actual non-empty metadata in the results to compare against
        has_any_valid_metadata = False
        for result in results:
            if result.get("score", 0.0) >= 0.6:
                for rec in result.get("recordings", []):
                    if rec.get("title") and rec.get("title").strip():
                        has_any_valid_metadata = True
                        break
                if has_any_valid_metadata:
                    break

        # 4. If we have a tagged MBID, check if it matches AcoustID
        if tagged_mbid:
            for result in results:
                if result.get("score", 0.0) >= 0.6:
                    for rec in result.get("recordings", []):
                        if rec.get("id") == tagged_mbid:
                            logger.info(f"[AcoustID] Track: '{tagged_title}' — Generated MBID: {tagged_mbid} | Expected MBID: {tagged_mbid} | Match: True")
                            return True, f"Verified by MusicBrainz Recording ID match ({tagged_mbid})"

            if require_recording_match:
                returned_mbids = sorted({
                    rec['id'] for result in results if float(result.get('score') or 0) >= .6
                    for rec in result.get('recordings', []) if rec.get('id')
                })
                if not returned_mbids:
                    return False, "No match found: insufficient AcoustID score or recording IDs"
                return False, (
                    f"AcoustID recording mismatch: expected {tagged_mbid}; "
                    f"returned {', '.join(returned_mbids)}. Artist/title alone cannot verify this version."
                )

        if not has_any_valid_metadata:
            return False, "No match found: insufficient AcoustID score or recording metadata"
        if not tagged_artist or not tagged_title:
            return False, "Insufficient local artist/title metadata for verification"

        # 5. Fallback: string matching
        import re
        from backend.app.sync import extract_main_artist, clean_search_title, get_artist_aliases
        def norm(s: str) -> str:
            if not s:
                return ""
            cleaned = re.sub(r'[^\w]', '', s).lower()
            if not cleaned:
                # If removing non-word characters completely emptied the string (e.g. '¥$', '!!!', '+/-'),
                # strip only whitespace so symbols are preserved.
                cleaned = re.sub(r'\s+', '', s).lower()
            return cleaned

        clean_tagged_title = norm(clean_search_title(tagged_title))
        norm_tagged_title = norm(tagged_title)
        
        main_art = extract_main_artist(tagged_artist)
        artist_aliases = [tagged_artist, main_art]
        norm_raw_art = norm(tagged_artist)
        backing_match = re.search(r"(?i)^(.+?)\s+(?:and|&)\s+the\s+", tagged_artist or "")
        if backing_match:
            lead = backing_match.group(1).strip()
            artist_aliases.append(lead)
            last_word = tagged_artist.split()[-1]
            artist_aliases.append(f"{lead} & The {last_word}")
        if norm_raw_art in ("kanyewest", "ye") or norm(main_art) in ("kanyewest", "ye"):
            artist_aliases.extend(["Kanye West", "Ye"])
        if norm_raw_art in ("¥$", "ys") or (tagged_artist and tagged_artist.strip() == "¥$"):
            artist_aliases.extend(["¥$", "Kanye West & Ty Dolla $ign", "Ye & Ty Dolla $ign"])
        elif any(k in norm_raw_art for k in ["kanyewest", "ye"]) and any(k in norm_raw_art for k in ["tydolla", "tydollasign"]):
            artist_aliases.extend(["¥$", "Kanye West & Ty Dolla $ign", "Ye & Ty Dolla $ign"])
        if "$" in (tagged_artist or ""):
            artist_aliases.append(tagged_artist.replace("$", "s"))
            artist_aliases.append(tagged_artist.replace("$", "S"))

        norm_aliases = list({norm(a) for a in artist_aliases if norm(a)})
        from backend.app.artist_identity import current_artist_aliases
        identity = current_artist_aliases()
        if identity:
            if not identity.names_for(tagged_artist or "") and not any(
                norm(artist.get("name", "")) == norm(tagged_artist)
                for result in results if float(result.get("score") or 0) >= .6
                for recording in result.get("recordings", []) for artist in recording.get("artists", [])
            ):
                await identity.ingest({"artist-credit": [
                    {"name": artist.get("name"), "artist": dict(artist)}
                    for result in results[:3] if float(result.get("score") or 0) >= .6
                    for recording in result.get("recordings", []) for artist in recording.get("artists", [])
                ]})
            norm_aliases.extend(norm(name) for name in identity.names_for(tagged_artist or ""))
        # AcoustID/MusicBrainz returns joint credits as separate artist entities,
        # while our expected credit can be a display string such as
        # "Drake & 21 Savage feat. Travis Scott". Only compare the complete
        # ordered credit here; do not split band names like "Florence and the Machine".
        credit_without_joiners = re.sub(
            r"(?i)\b(?:feat(?:uring)?|ft|and|with|x)\b\.?", "", tagged_artist or ""
        )
        norm_joint_credit = norm(credit_without_joiners)
        if not norm_joint_credit and norm_raw_art in ("¥$", "ys"):
            norm_joint_credit = norm("Kanye West Ty Dolla $ign")

        def explicit_credit_parts(artist: str) -> list[str]:
            """Split only explicit joint credits, never ordinary band names."""
            # Backing bands like 'Prince and The Revolution' or 'Bob Marley & The Wailers'
            # should not be split into mandatory separate artist entities.
            if re.search(r"(?i)\s+(?:and|&)\s+the\s+", artist or ""):
                return []
            if not re.search(r"(?i)&|,|\b(?:and|with|x|feat(?:uring)?|ft)\b", artist or ""):
                return []
            parts = re.split(r"(?i)\s*(?:&|,|\band\b|\bwith\b|\bx\b|\bfeat(?:uring)?\b|\bft)\.?\s*", artist)
            return [norm(part) for part in parts if norm(part)]

        expected_credit_parts = explicit_credit_parts(tagged_artist or "")
        if not expected_credit_parts and (norm_raw_art in ("¥$", "ys") or (tagged_artist and tagged_artist.strip() == "¥$")):
            expected_credit_parts = [norm("Kanye West"), norm("Ty Dolla $ign")]

        best_match_desc = ""
        highest_score = 0.0

        for result in results:
            score = float(result.get("score") or 0.01)
            if score < 0.6:
                continue

            for rec in result.get("recordings", []):
                rec_title = norm(rec.get("title", ""))
                clean_rec_title = norm(clean_search_title(rec.get("title", "")))

                title_match = bool(clean_tagged_title and clean_rec_title and clean_tagged_title == clean_rec_title)
                if not title_match:
                    title_match = bool(norm_tagged_title and rec_title and norm_tagged_title == rec_title)

                if title_match:
                    rec_artists = rec.get("artists") or []
                    artist_match = False
                    if not rec_artists:
                        artist_match = not tagged_artist
                    else:
                        for art in rec_artists:
                            art_name = art.get("name", "")
                            if identity and identity.ids_for(tagged_artist or "") and art.get("id") and not identity.same_artist(tagged_artist or "", art_name, art["id"]):
                                continue
                            art_candidates = [art_name]
                            rec_backing = re.search(r"(?i)^(.+?)\s+(?:and|&)\s+the\s+", art_name or "")
                            if rec_backing:
                                art_candidates.append(rec_backing.group(1).strip())
                            if norm(art_name) in ("kanyewest", "ye"):
                                art_candidates.extend(["Kanye West", "Ye"])
                            if art_name == "¥$" or norm(art_name) in ("¥$", "ys"):
                                art_candidates.extend(["¥$", "Kanye West & Ty Dolla $ign", "Kanye West", "Ty Dolla $ign", "Ye"])
                            elif "$" in art_name:
                                art_candidates.append(art_name.replace("$", "s"))
                            for cand in art_candidates:
                                rec_art = norm(cand)
                                same_identity = identity and identity.same_artist(tagged_artist or "", cand, art.get("id"))
                                if same_identity or (rec_art and any(alias == rec_art for alias in norm_aliases if alias)):
                                    artist_match = True
                                    break
                            if artist_match:
                                break
                        if not artist_match and len(rec_artists) > 1 and norm_joint_credit:
                            joined_credit = norm("".join(art.get("name", "") for art in rec_artists))
                            artist_match = joined_credit == norm_joint_credit
                            if not artist_match and len(expected_credit_parts) > 1:
                                # AcoustID may include a featured artist that
                                # is absent from the local expected tag. The
                                # expected explicit credit must still appear
                                # in order; unrelated or missing artists fail.
                                recording_parts = [
                                    norm(art.get("name", "")) for art in rec_artists
                                ]
                                expected_idx = 0
                                for recording_part, credited in zip(recording_parts, rec_artists):
                                    same_identity = identity and identity.same_artist(
                                        expected_credit_parts[expected_idx], credited.get("name", ""), credited.get("id")
                                    )
                                    if recording_part == expected_credit_parts[expected_idx] or same_identity:
                                        expected_idx += 1
                                        if expected_idx == len(expected_credit_parts):
                                            artist_match = True
                                            break

                    # Do not accept a similarly named song by an unrelated artist.
                    if artist_match:
                        if score > highest_score:
                            highest_score = score
                            art_name = rec_artists[0].get("name") if rec_artists else "Unknown"
                            best_match_desc = f"Matched AcoustID recording '{rec.get('title')}' by '{art_name}' (score: {score:.2f})"

        generated_mbid = None
        for r in results:
            if r.get("score", 0.0) >= 0.6:
                for rec in r.get("recordings", []):
                    if rec.get("id"):
                        generated_mbid = rec.get("id")
                        break
            if generated_mbid: break

        if highest_score > 0.0:
            logger.info(f"[AcoustID] Track: '{tagged_title}' — Generated MBID: {generated_mbid} | Expected MBID: {tagged_mbid} | Match: True")
            return True, best_match_desc

        logger.info(f"[AcoustID] Track: '{tagged_title}' — Generated MBID: {generated_mbid} | Expected MBID: {tagged_mbid} | Match: False")
        # If we got here, it's a mismatch
        top_matches = []
        for result in results[:3]:
            for rec in result.get("recordings", []):
                artists_str = ", ".join(a.get("name", "Unknown") for a in (rec.get("artists") or []))
                top_matches.append(f"'{rec.get('title')}' by {artists_str}")
        matches_str = " | ".join(top_matches) if top_matches else "No metadata available"
        return False, f"AcoustID matched this audio to: {matches_str} (Expected: '{tagged_artist} - {tagged_title}')"

    async def verify_track(
        self,
        file_path: Path,
        expected_mbid: str,
        expected_artist: Optional[str] = None,
        expected_title: Optional[str] = None,
    ) -> bool:
        """
        Returns True if the track matches the expected MBID, or if AcoustID matches the track metadata.
        Returns False ONLY if AcoustID successfully checked and fingerprint belongs to a different track.
        """
        # Generate/lookup once, then compare identity and text against that response.
        is_valid, reason = await self.verify_track_against_metadata(
            file_path, expected_artist=expected_artist, expected_title=expected_title,
            expected_mbid=expected_mbid,
        )
        if verification_status(is_valid, reason) != "mismatch":
            logger.info("AcoustID result for %s: %s", file_path, reason)
            return True

        logger.warning(f"AcoustID MISMATCH: {file_path} - {reason}")
        return False

acoustid_client = AcoustIDClient()
