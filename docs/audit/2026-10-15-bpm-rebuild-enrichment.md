# Missing BPM in Metadata Rebuild

Follow-up to the preservation fix in `2026-10-15-bpm-mood-tags.md`.

## Identity versus audio features

A MusicBrainz recording MBID identifies a recording; it does not itself contain
tempo or a standardized mood classification. MusicBrainz folksonomy tags can
contain mood-like descriptions, but are community tags and not calibrated audio
analysis. See the official [MusicBrainz API](https://musicbrainz.org/doc/MusicBrainz_API)
and [Picard tag documentation](https://picard-docs.musicbrainz.org/en/latest/variables/tags_basic.html).

[AcousticBrainz](https://acousticbrainz.org/) stores low-level audio analysis
(including BPM) and high-level mood classifications keyed by recording MBID.
It stopped collecting data in 2022, so it is an optional archive, not a complete
source for modern releases. Its API still answered a read-only check during this
implementation. No user audio was uploaded.

## Implemented workflow

- New scans default to looking up **missing** BPM; the UI switch can disable it.
- Match the local track to a MusicBrainz release/recording first. No title search
  or artist/album-level BPM substitution is performed.
- Query the fixed HTTPS AcousticBrainz low-level endpoint using a validated
  recording UUID. Check the returned recording ID when present and compare the
  analysis duration against the local file when both are available. Differences
  over five seconds or 2%, whichever is greater, prevent a proposal.
- Preserve existing BPM, including fractional values. Do not query it again.
- Display missing, proposed and unresolved counts plus per-track lookup status.
  Proposals are explicitly labeled as audio-analysis estimates, not ground truth.
- Scanning never changes files. Applying a reviewed album writes the proposed
  BPM using the existing format-aware writer and backup/rollback pipeline.
- Explicit release selection recomputes BPM proposals using the new recording
  identity and retains the scan's lookup setting.
- An AcoustID mismatch suppresses optional BPM enrichment during verified scans.

There is no local audio-analysis engine and no automatic mood enrichment in this
change. Newly downloaded untagged files are not automatically analyzed. Missing
archive data remains visibly unresolved rather than being replaced with a guess.

## Provider behavior

- Persistent SQLite provider cache: found values 30 days, misses 7 days, failures
  5 minutes. An additional bounded in-process cache avoids repeated calls.
- Five-second HTTP timeout, serialized calls spaced by at least 300 ms, a
  provider-wide one-minute cooldown on errors, and numeric Retry-After/reset
  headers honored within a ten-minute cap. No retry loop per library track.
- Optional provider/cache failures do not discard MusicBrainz release proposals.
- Estimates are presented with two decimal places at most; this does not imply
  two-decimal accuracy. MP4 `tmpo` remains integer-valued as documented previously.

## How to use

Deploy beta, open Metadata Rebuild, leave **Look up missing BPM** enabled and run
a **new** library scan. Old saved plans are not silently enriched on page load.
Inspect BPM proposals and unresolved reasons, then apply only reviewed albums.
The existing apply path refreshes the local index and requests a Navidrome scan.
No library scan, retag, album download or NAS modification was initiated as part
of development verification.

## Verification

Backend tests cover exact UUID requests, persistent cache across Database
instances, negative caching, outage cooldown, malformed tempo, wrong recording,
duration mismatch, disabled lookup, unchanged existing BPM, read-only proposals,
explicit release changes, apply/rollback, optional provider failures and AcoustID
mismatches. Frontend tests cover missing/proposed/preserved display, counts and
the scan option. A real archive API response was checked for recording
`099b148e-fe99-4b79-be6e-5078e4bb7415`; it returned a tempo and recording ID.
