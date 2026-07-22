import urllib.parse
import httpx
import asyncio
import re
import os
from typing import List, Dict, Any, Optional, Tuple
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from backend.app.logger import get_logger
from backend.app.clients.http_client import get_http_client

logger = get_logger()

class SlskdClient:
    def __init__(self, base_url: str, api_key: str = "", timeout: int = 20):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "User-Agent": "VeryDisco/1.0.0",
            "Content-Type": "application/json"
        }
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True
    )
    async def create_search(self, query: str) -> str:
        """Start a search in slskd and return search ID."""
        url = f"{self.base_url}/api/v0/searches"
        payload = {"searchText": query}
        logger.info(f"Triggering slskd search for query: '{query}'")
        client = get_http_client()
        resp = await client.post(url, json=payload, headers=self._get_headers())
        resp.raise_for_status()
        data = resp.json()
        search_id = data.get("id")
        if not search_id:
            raise ValueError("slskd did not return a search ID")
        return search_id

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True
    )
    async def get_search_status(self, search_id: str) -> Tuple[bool, int, int]:
        """Check if search is complete. Returns (isComplete, fileCount, lockedFileCount)."""
        url = f"{self.base_url}/api/v0/searches/{search_id}"
        client = get_http_client()
        resp = await client.get(url, headers=self._get_headers())
        resp.raise_for_status()
        data = resp.json()
        return data.get("isComplete", False), data.get("fileCount", 0), data.get("lockedFileCount", 0)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True
    )
    async def get_search_responses(self, search_id: str) -> List[Dict[str, Any]]:
        """Retrieve results for a search ID."""
        url = f"{self.base_url}/api/v0/searches/{search_id}/responses"
        client = get_http_client()
        resp = await client.get(url, headers=self._get_headers())
        resp.raise_for_status()
        return resp.json()

    def _parse_candidates(
        self,
        responses: List[Dict[str, Any]],
        artist: str,
        title: str,
        audio_quality: dict,
        album: Optional[str] = None,
        query: str = "",
        filter_quality: bool = True
    ) -> List[Dict[str, Any]]:
        """Filters and prioritizes search result file candidates based on quality and matching criteria."""
        candidates = []
 
        # Clean trailing feature/remaster annotations (explo-like normalization)
        FEAT_TAIL_RE = re.compile(r'(?i)\s*[\(\[\{]\s*(feat\.?|featuring|ft\.?|with)\s[^\)\]\}]*[\)\]\}]\s*$')
        REMASTER_TAIL_RE = re.compile(r'(?i)\s*[-–—]\s*\d{4}\s*remaster(ed)?\s*$')

        # Helper to normalize strings for comparison
        def normalize(s: str) -> str:
            return re.sub(r'[^\w]', '', s).lower()

        def normalize_title(s: str) -> str:
            s = FEAT_TAIL_RE.sub('', s)
            s = REMASTER_TAIL_RE.sub('', s)
            return normalize(s)
            
        norm_artist = normalize(artist)
        norm_title = normalize_title(title)
        norm_album = normalize(album) if album else ""
        artist_words = [normalize(w) for w in artist.split() if len(w) > 2]
        
        preset = audio_quality.get("preset", "lossless")
        custom_profiles = audio_quality.get("custom_profiles", [])

        # Resolve which profile list to use based on preset
        if preset == "lossless":
            from backend.app.config import LOSSLESS_PRESETS_DEFAULT
            active_profiles = [dict(p) for p in LOSSLESS_PRESETS_DEFAULT]
        elif preset == "storage_saver":
            from backend.app.config import STORAGE_SAVER_PRESETS_DEFAULT
            active_profiles = [dict(p) for p in STORAGE_SAVER_PRESETS_DEFAULT]
        elif preset == "custom":
            active_profiles = [
                p if isinstance(p, dict) else p.model_dump()
                for p in custom_profiles
            ]
        else:
            active_profiles = []  # accept everything

        def get_quality_priority(filename: str, bitrate: int, bit_depth: int, sample_rate: int) -> int:
            """Return profile list index (0 = best) if the file matches a profile, or -1 to reject."""
            ext = os.path.splitext(filename)[1].lower().strip(".")

            if not active_profiles:
                return 0  # no filter — accept all

            for i, prof in enumerate(active_profiles):
                fmt = prof.get("format", "").lower().strip(".")
                if ext != fmt:
                    continue

                min_br = prof.get("min_bitrate", 0) or 0
                max_br = prof.get("max_bitrate", 0) or 0
                req_depth = prof.get("bit_depth", 0) or 0
                req_sr = prof.get("sample_rate", 0) or 0

                # ---- bitrate check (skip if reported bitrate is 0 / unknown) ----
                if bitrate > 0:
                    if min_br > 0 and bitrate < min_br:
                        continue
                    if max_br > 0 and bitrate > max_br:
                        continue

                # ---- bit depth check (skip if reported depth is 0 / unknown) ----
                if bit_depth > 0 and req_depth > 0:
                    if bit_depth != req_depth:
                        continue

                # ---- sample rate check (skip if reported rate is 0 / unknown) ----
                if sample_rate > 0 and req_sr > 0:
                    if sample_rate != req_sr:
                        continue

                return i  # matched — lower index = higher preference

            return -1  # no profile matched

        for peer in responses:
            username = peer.get("username")
            has_free_slot = peer.get("hasFreeUploadSlot", True)
            queue_length = peer.get("queueLength", 0)
            speed = peer.get("speed", 0)
 
            for f in peer.get("files", []):
                # Use name first, fallback to filename
                filename = f.get("filename") or f.get("name") or ""
                if not filename:
                    continue
 
                # Filename matching: title must match AND (artist must match OR album must match)
                norm_filename = normalize(filename)
                if norm_title not in norm_filename:
                    continue
                    
                from backend.app.sync import get_artist_aliases
                norm_artists = get_artist_aliases(artist)
                artist_match = any(a in norm_filename for a in norm_artists) or any(word in norm_filename for word in artist_words)
                album_match = (norm_album in norm_filename) if norm_album else False
                
                if not (artist_match or album_match):
                    continue
 
                # Keyword filtering: Strict release filtering (Explo logic)
                keywords = [
                    "mashup", "bootleg", "dj set", "live", "remix", "instrumental", 
                    "acoustic", "demo", "karaoke", "cover", "edit", "mix", "vip",
                    "chopped", "screwed", "slowed", "chopnotslop"
                ]
                is_keyword_skipped = False
                for kw in keywords:
                    # If keyword is in the track's true title or artist, we allow it in the filename
                    if kw in norm_title or kw in norm_artist:
                        continue
                    # If keyword is in the filename but NOT in the track title/artist, skip it
                    if kw in filename.lower():
                        is_keyword_skipped = True
                        break
                
                if is_keyword_skipped:
                    continue
 
                bitrate = f.get("bitrate") or f.get("bitRate") or 0
                bit_depth = f.get("bitDepth") or f.get("bit_depth") or 0
                sample_rate = f.get("sampleRate") or f.get("sample_rate") or 0
                priority = get_quality_priority(filename, bitrate, bit_depth, sample_rate)
                if priority < 0:
                    if filter_quality:
                        continue
                    else:
                        priority = 999
 
                # Correct size mapping: length is duration in seconds, size is in bytes
                size = f.get("size") or 0
                duration = f.get("length") or 0
 
                candidates.append({
                    "username": username,
                    "filename": filename,
                    "size": size,
                    "duration": duration,
                    "bitrate": bitrate,
                    "hasFreeUploadSlot": has_free_slot,
                    "queueLength": queue_length,
                    "speed": speed,
                    "priority": priority
                })
 
        # Sort candidates
        candidates.sort(key=lambda x: (
            x["priority"],
            not x["hasFreeUploadSlot"],
            x["queueLength"],
            -x["speed"]
        ))
 
        return candidates

    async def search_candidates(
        self,
        artist: str,
        title: str,
        query: str,
        audio_quality: dict,
        album: Optional[str] = None,
        search_timeout: int = 90,
        filter_quality: bool = True
    ) -> Tuple[List[Dict[str, Any]], str]:
        """Performs search, polls until complete or timeout, and returns sorted file candidates."""
        search_id = await self.create_search(query)
        
        # Poll search status
        elapsed = 0
        poll_interval = 2  # Poll faster (every 2 seconds)
        is_complete = False
        file_count = 0
        
        logger.info(f"Polling slskd search status for ID '{search_id}'...")
        while elapsed < search_timeout:
            try:
                is_complete, file_count, locked_count = await self.get_search_status(search_id)
                
                # Fetch partial responses and check if we already have matching candidates!
                if file_count > 0:
                    responses = await self.get_search_responses(search_id)
                    candidates = self._parse_candidates(
                        responses=responses,
                        artist=artist,
                        title=title,
                        audio_quality=audio_quality,
                        album=album,
                        query=query,
                        filter_quality=filter_quality
                    )
                    
                    if candidates:
                        if is_complete:
                            logger.info(f"slskd search '{search_id}' complete. Found {len(candidates)} candidates.")
                            return candidates, search_id
                            
                        # Early exit conditions:
                        # 1. We have a solid pool of candidates (e.g. 15+)
                        if len(candidates) >= 15:
                            logger.info(f"slskd search '{search_id}' accumulated {len(candidates)} matching candidates. Proceeding early.")
                            return candidates, search_id
                            
                        # 2. Or we have waited at least 10s and found at least some candidates (e.g. 1+)
                        if elapsed >= 10 and len(candidates) >= 1:
                            logger.info(f"slskd search '{search_id}' has {len(candidates)} matching candidates after {elapsed}s. Proceeding early.")
                            return candidates, search_id
                
                if is_complete:
                    logger.info(f"slskd search '{search_id}' marked as complete ({file_count} files). No matching candidates found.")
                    break
            except Exception as e:
                logger.warning(f"Error checking search status: {e}")
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        if not is_complete and file_count == 0:
            logger.warning(f"slskd search '{search_id}' did not complete within {search_timeout}s and found 0 files.")

        # Get final results
        responses = await self.get_search_responses(search_id)
        candidates = self._parse_candidates(
            responses=responses,
            artist=artist,
            title=title,
            audio_quality=audio_quality,
            album=album,
            query=query
        )
        logger.info(f"Found {len(candidates)} candidates matching criteria for '{query}'")
        return candidates, search_id
 
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True
    )
    async def request_download(self, username: str, filename: str, size: int) -> bool:
        """Triggers download from a specific peer for a file."""
        encoded_user = urllib.parse.quote(username)
        url = f"{self.base_url}/api/v0/transfers/downloads/{encoded_user}"
        
        payload = [{
            "filename": filename,
            "size": size
        }]
        
        logger.info(f"Requesting download from peer '{username}' for file '{filename}' ({size} bytes)")
        client = get_http_client()
        resp = await client.post(url, json=payload, headers=self._get_headers())
        if resp.status_code in [200, 201, 202]:
            return True
        logger.error(f"Failed to request download for '{filename}' from '{username}'. Status: {resp.status_code}, Response: {resp.text}")
        return False
 
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True
    )
    async def get_download_status(self, username: str, filename: str, size: int) -> Tuple[str, Optional[str]]:
        """
        Check the status of a specific download.
        Returns (status_string, file_id_or_none).
        """
        encoded_user = urllib.parse.quote(username)
        url = f"{self.base_url}/api/v0/transfers/downloads/{encoded_user}"
        
        client = get_http_client()
        resp = await client.get(url, headers=self._get_headers())
        
        if resp.status_code == 404:
            return "failed", None
        resp.raise_for_status()
        
        data = resp.json()
        transfers = data if isinstance(data, list) else [data]
        
        matched_state = "downloading"
        matched_id = None
        found = False
        for transfer in transfers:
            for directory in transfer.get("directories", []):
                for file_info in directory.get("files", []):
                    if file_info.get("filename") == filename:
                        actual_size = file_info.get("size", 0)
                        size_ok = True
                        if size > 0 and actual_size > 0:
                            size_ok = abs(actual_size - size) / size < 0.05
                        
                        if size_ok:
                            matched_state = file_info.get("state", "").lower()
                            matched_id = file_info.get("id")
                            found = True
                            break
                        
        if found:
            if "succeeded" in matched_state:
                return "succeeded", matched_id
            elif any(x in matched_state for x in ["error", "abort", "cancel", "fail", "time"]):
                logger.warning(f"Download state for '{filename}' from '{username}' is '{matched_state}'")
                return "failed", matched_id
            return "downloading", matched_id
                        
        return "downloading", None

    async def get_download_progress(self, username: str, filename: str, size: int) -> Tuple[str, Optional[str], int]:
        """
        Check progress of a specific download.
        Returns (status_string, file_id, bytes_transferred).
        """
        encoded_user = urllib.parse.quote(username)
        url = f"{self.base_url}/api/v0/transfers/downloads/{encoded_user}"
        
        try:
            client = get_http_client()
            resp = await client.get(url, headers=self._get_headers())
            if resp.status_code == 404:
                return "failed", None, 0
            resp.raise_for_status()
            
            data = resp.json()
            transfers = data if isinstance(data, list) else [data]
            
            matched_state = "downloading"
            matched_id = None
            bytes_tx = 0
            found = False
            for transfer in transfers:
                for directory in transfer.get("directories", []):
                    for file_info in directory.get("files", []):
                        if file_info.get("filename") == filename:
                            actual_size = file_info.get("size", 0)
                            size_ok = True
                            if size > 0 and actual_size > 0:
                                size_ok = abs(actual_size - size) / size < 0.05
                            
                            if size_ok:
                                matched_state = file_info.get("state", "").lower()
                                matched_id = file_info.get("id")
                                bytes_tx = file_info.get("bytesTransferred", 0)
                                found = True
                                break
                                
            if found:
                if "succeeded" in matched_state:
                    return "succeeded", matched_id, bytes_tx
                elif any(x in matched_state for x in ["error", "abort", "cancel", "fail", "time"]):
                    logger.warning(f"Download state for '{filename}' from '{username}' is '{matched_state}'")
                    return "failed", matched_id, bytes_tx
                return "downloading", matched_id, bytes_tx
                            
            return "downloading", None, 0
        except Exception as e:
            logger.warning(f"Failed to check progress: {e}")
            return "downloading", None, 0


    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True
    )
    async def get_all_downloads(self) -> List[Dict[str, Any]]:
        """Fetch all downloads across all users from slskd."""
        url = f"{self.base_url}/api/v0/transfers/downloads"
        client = get_http_client()
        resp = await client.get(url, headers=self._get_headers())
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else [data]

    async def get_peer_downloads(self, username: str) -> List[Dict[str, Any]]:
        """Fetch downloads for a specific peer from slskd."""
        encoded_user = urllib.parse.quote(username)
        url = f"{self.base_url}/api/v0/transfers/downloads/{encoded_user}"
        client = get_http_client()
        resp = await client.get(url, headers=self._get_headers())
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else [data]

    async def delete_search(self, search_id: str) -> bool:
        """Delete a search from slskd to clean up."""
        url = f"{self.base_url}/api/v0/searches/{search_id}"
        client = get_http_client()
        try:
            resp = await client.delete(url, headers=self._get_headers())
            return resp.status_code in [200, 204]
        except Exception as e:
            logger.warning(f"Failed to delete search {search_id}: {e}")
            return False

    async def delete_download(self, username: str, file_id: str) -> bool:
        """Cancel and remove a download from slskd transfers."""
        encoded_user = urllib.parse.quote(username)
        url_soft = f"{self.base_url}/api/v0/transfers/downloads/{encoded_user}/{file_id}?remove=false"
        url_hard = f"{self.base_url}/api/v0/transfers/downloads/{encoded_user}/{file_id}?remove=true"
        client = get_http_client()
        try:
            # 1. Cancel / soft delete
            await client.delete(url_soft, headers=self._get_headers())
            await asyncio.sleep(1)
            # 2. Hard remove from transfer list
            resp = await client.delete(url_hard, headers=self._get_headers())
            return resp.status_code in [200, 204]
        except Exception as e:
            logger.warning(f"Failed to delete download {file_id}: {e}")
            return False
