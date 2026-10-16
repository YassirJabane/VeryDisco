# Album-bound missing tracks

Download Missing requires the existing album folder and carries the selected
release, recording MBID and disc/track positions from the UI. The server verifies
folder membership in the authenticated user's catalog, reads existing tags and
resolves that exact MusicBrainz release once before queueing anything. Conflicting
release IDs, mixed album tags, wrong positions and ambiguous titles reject the
whole request. A missing/unknown release requires verifying the album first.

Downloads inherit the local album title/artist/date and exact folder, even when
its spelling differs from the provider's canonical name. Existing disc folder
style (e.g. CD2) is retained. A missing disc folder uses Disc 02 etc. Each track
retains its selected recording/disc/position; the downloader never reselects an
edition. Existing recordings are skipped, repeated requests are deduplicated
while running, and atomic create-only publication refuses collisions. Filesystems
without hardlink support fail safely instead of overwriting existing files.

This fixes future downloads only. Existing split albums are not migrated,
retagged, moved or deleted automatically.

# Reviewed audio enrichment

Metadata Rebuild adds switches for integer BPM, archive audio features and
optional local analysis. Newly proposed BPM defaults to rounded integers in the
UI/API; the original estimate and source remain in the saved plan. Disabling
rounding preserves fractional estimates. Existing BPM/key values are preserved.

AcousticBrainz low-level results now include key/scale/strength, derived Camelot
notation, danceability, onset rate, tempo histogram peaks and dynamics/loudness
when present. These are estimates from an archive that stopped collecting in
2022, not verified facts or measurements of the user's exact file. High-level
mood/genre/voice classifiers are shown separately with model scores and never
automatically mark tracks instrumental or overwrite genres/mood. Missing scores
are never invented. Archive loudness is never written as ReplayGain.

Missing key can be proposed as a standard musical-key tag (TKEY/initialkey/MP4
freeform), while full descriptors stay in the persisted plan for review. Apply
remains explicit, with rollback backup; scans never write media files.

## Optional Essentia local worker

The lightweight default image does not include Essentia. For the project's
Python 3.12 Linux x86_64 runtime, build with:

```sh
docker build --build-arg WITH_AUDIO_ANALYSIS=true -t verydisco-audio .
```

The optional dependencies are pinned in backend/requirements-audio.txt to a
published CPython 3.12 Linux wheel. Other architectures/platforms require a
compatible Essentia installation; this build option is not claimed portable to
ARM or Windows. Review the upstream library and model licensing before shipping
additional analyzers/models. No commercial-provider credentials or audio uploads
are configured by this implementation.

Enable Analyze locally with Essentia before starting a Metadata Rebuild scan.
One subprocess runs at a time, with a 180-second per-file timeout and cancellation
cleanup. The worker analyzes the file read-only and returns scalar descriptors;
no network upload, tag writing or external service is involved. Local measurements
take precedence over archive descriptors without replacing existing manual tags.
Successful results are cached by resolved path, size, nanosecond mtime and analyzer
schema version. File changes invalidate the cache. If Essentia is absent, the
plan reports not_installed instead of pretending the analysis succeeded.

This integration uses the low-level MusicExtractor, not downloaded ML models.
Mood/genre classifications therefore come only from optional AcousticBrainz
high-level lookups in this version. AudioMuse-AI, GetSongBPM and paid cloud analysis
were evaluated as alternatives, not silently enabled or installed.
