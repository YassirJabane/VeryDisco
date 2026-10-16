# UI thumbnail pipeline

Explore and My Feedback prefer the CAA image identity supplied by ListenBrainz
(`caa_release_mbid` and `caa_id`) and request a 250px derivative. Missing artwork
metadata is fetched from ListenBrainz by recording MBID in batches of 50, with
four requests at most in flight and a five-second total budget. Results are
cached in memory for six hours (failures for one minute). A metadata lookup
cannot replace a known album with another edition. Exact artist/title/album
matching on Deezer remains the fallback when metadata is unavailable; it also
handles a CAA image that fails to load.

Shared avatar rendering uses 250px variants for known Deezer, CAA and iTunes
URLs. An IntersectionObserver defers supplying the URL to MUI until the avatar
approaches the viewport, because MUI otherwise preloads even a lazy image.
Unknown/custom URLs are preserved.

Library covers are JPEG derivatives, 160px by default (32–512px accepted),
preserving aspect ratio and never upscaling. Imported playlist lists use 250px
derivatives of existing local sidecars. Spotify previews prefer a supplied
small variant when available; custom URLs or providers with no size variant
can still use an original. No remote image proxy or HTML scraping is added.

Local derivatives have a bounded 256-entry memory cache, invalidated by source
filenames, sizes and modification times. HTTP responses use private browser
caching for five minutes and ETags. Authorization and directory scope checks
happen before cached artwork is served. Originals and audio-file artwork keep
their original resolution. Pillow is a backend dependency; rebuild the backend
image/install requirements and rebuild the frontend to deploy the change.

API references: [ListenBrainz recording metadata](https://listenbrainz.readthedocs.io/en/latest/users/api/metadata.html)
and [Cover Art Archive thumbnails](https://musicbrainz.org/doc/Cover_Art_Archive/API).
