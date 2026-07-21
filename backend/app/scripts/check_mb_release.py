"""
check_mb_release.py

CLI tool to query MusicBrainz for an artist + album and see how candidate releases are scored,
showing which release gets selected as the authoritative multi-disc release.

Usage:
  python3 backend/app/scripts/check_mb_release.py "Drake" "Scorpion"
"""

import sys
import os
import asyncio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))

from backend.app.clients.musicbrainz import _mb_get

def _normalize(s: str) -> str:
    import re
    return re.sub(r"[^\w]", "", s).lower()

def _fuzzy_title_match(a: str, b: str) -> bool:
    na, nb = _normalize(a), _normalize(b)
    return na == nb or na in nb or nb in na

def _score_release(r: dict, album: str) -> int:
    score = 0
    title = r.get("title", "")
    status = (r.get("status") or "").lower()
    country = r.get("country") or r.get("release-event-count", "")
    disambiguation = (r.get("disambiguation") or "").lower()
    media = r.get("media") or []
    formats = {(m.get("format") or "").lower() for m in media}
    rg = r.get("release-group") or {}
    secondary_types = [t.lower() for t in (rg.get("secondary-types") or [])]

    if status == "official":
        score += 20
    if "cd" in formats:
        score += 15
    elif "digital media" in formats and "cd" not in formats:
        score -= 5

    if isinstance(country, str):
        if country.upper() == "XW":
            score += 10
        elif country.upper() in ("US", "GB"):
            score += 5

    if _normalize(title) == _normalize(album):
        score += 10
    elif _fuzzy_title_match(title, album):
        score += 5

    if "explicit" in disambiguation:
        score += 5
    if "clean" in disambiguation or "instrumental" in disambiguation or "karaoke" in disambiguation:
        score -= 15

    for bad_type in ("live", "compilation", "remix", "dj-mix", "spokenword", "mixtape"):
        if bad_type in secondary_types:
            score -= 20

    return score


async def main():
    if len(sys.argv) < 3:
        print("Usage: python3 check_mb_release.py \"Artist\" \"Album\"")
        sys.exit(1)

    artist = sys.argv[1]
    album = sys.argv[2]

    print(f"\n=======================================================")
    print(f" Searching MusicBrainz for: '{artist}' - '{album}'")
    print(f"=======================================================\n")

    data = await _mb_get("/release", params={
        "query": f'release:"{album}" AND artist:"{artist}"',
        "limit": 20,
        "inc": "artist-credits+media+release-groups",
        "fmt": "json",
    })
    releases = data.get("releases", []) if data else []

    if not releases:
        print("❌ No releases found on MusicBrainz.")
        return

    candidates = [r for r in releases if _fuzzy_title_match(r.get("title", ""), album)]
    if not candidates:
        candidates = releases

    scored = []
    for r in candidates:
        s = _score_release(r, album)
        scored.append((s, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_release = scored[0]

    print(f"Found {len(candidates)} candidate releases. Ranked by score:\n")
    print(f"{'SCORE':<7} | {'STATUS':<9} | {'CTRY':<4} | {'FORMATS':<18} | {'TITLE / DISAMBIGUATION'}")
    print("-" * 75)

    for score, r in scored:
        title = r.get("title", "")
        status = r.get("status", "?")
        country = r.get("country", "?")
        media = r.get("media") or []
        formats = ",".join([m.get("format") or "?" for m in media]) or "?"
        disamb = r.get("disambiguation", "")
        dis_str = f" ({disamb})" if disamb else ""
        is_best = " 🏆 [WINNER]" if r["id"] == best_release["id"] else ""

        print(f"{score:<7} | {status:<9} | {country:<4} | {formats:<18} | {title}{dis_str}{is_best}")

    best_mbid = best_release["id"]
    print(f"\n-------------------------------------------------------")
    print(f" Selected Release MBID: {best_mbid}")
    print(f" Title: {best_release.get('title')}")
    print(f"-------------------------------------------------------\n")

    full = await _mb_get(f"/release/{best_mbid}", params={
        "inc": "media+recordings",
        "fmt": "json",
    })

    if not full:
        print("Could not fetch release media details.")
        return

    media_list = full.get("media", [])
    print(f"Discs Count: {len(media_list)}\n")

    for m in media_list:
        disc_num = m.get("position", 1)
        tracks = m.get("tracks", [])
        print(f"--- Disc {disc_num:02d} ({len(tracks)} tracks, format: {m.get('format', 'CD')}) ---")
        for t in tracks:
            pos = t.get("position") or t.get("number")
            rec = t.get("recording") or {}
            print(f"  [{disc_num:02d}-{int(pos):02d}] {rec.get('title') or t.get('title')}")
        print()

if __name__ == "__main__":
    asyncio.run(main())
