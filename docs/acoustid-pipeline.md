# AcoustID checks and operation-scoped artist aliases

Downloads and library scans distinguish verified audio, a positive mismatch,
unknown fingerprints/insufficient evidence, unavailable API responses, and
skipped checks. Only a positive mismatch rejects a new download candidate or
offers replacement in the AcoustID page. Existing historical "No match found"
rows are shown as inconclusive rather than proof of a bad audio file.

API requests share a process-wide gate (two calls per second). Transport errors,
HTTP 408/429/5xx, invalid JSON and transient API errors receive up to three
attempts with exponential backoff. Invalid credentials and other permanent
errors are not retried. Empty successful results are unknown, not an API error.
No fingerprint/API key is written to diagnostic logs; result counts, best score,
expected recording ID and outcomes are logged instead.

One fingerprint/lookup is used for the recording-ID check and text fallback.
An exact expected recording ID can verify even if text is missing; text matches
must meet the same 0.6 score floor. Download checks never trust a peer file's
embedded ID over explicitly requested identity. Local scans avoid guesses from
folder names when artist/title tags are missing and read all artist tag values.

The AcoustID page has **Recheck audio** for each unsuccessful result and
**Recheck all unsuccessful** for an explicit retry of unchanged scanned files.
Rechecks are scoped to the authenticated user's catalog and music root, update
only verification results, and never alter tags, replace audio or start a search.
A file changed during verification cannot receive the old result. Successful
checks disappear from the review list. Background scans and manual rechecks for
the same user are serialized.

Download operations hold a ContextVar-backed artist identity context. It collects
MusicBrainz credits/aliases from existing responses, fetches missing aliases at
most once per artist MBID, and is shared by all album tracks, peer retries and
inherited single-track fallback tasks. A subsequent operation starts a fresh
context, including on album metadata-cache hits. Alias lists are never saved to
the DB/config or long-lived album caches. Stable artist MBIDs/credited names can
be cached. Artist-name aliases participate in matching; legal names, search hints
and untyped aliases are search aids only. Alias collisions across IDs are not
treated as identity equivalence. Display/tag credits remain unchanged.

The update is local source code and requires rebuilding/deploying the app. It
does not change existing audio or verification rows on the production server.

## Incremental scan counters

Statistics, Scan Remaining and manual rechecks now use the same user-scoped
library-index catalog and path exclusions: Explore/Explore Tracks, playlists
(including the configured playlist directory), staging and hidden directories
are excluded. Historical results outside the current catalog cannot inflate the
counts. All indexed audio formats share the library reader's suffix list.
The old filesystem total cache is no longer used.

A result counts as scanned only when its size and nanosecond mtime match the
indexed file. Thus scanned + remaining = total, and verified + mismatches +
inconclusive = scanned. Unknown/skipped/API errors remain inconclusive, not
mismatches, and are retried only through the explicit unsuccessful-file recheck
(or when the file changes). If an outdated index reselects already-checked audio,
Scan Remaining checks the actual file signature and refreshes the index without
another fingerprint/API request. Index freshness still depends on library scans
and point refreshes; changes outside the app require refreshing the library.

## Duration and decoder diagnostics

The audio duration used for lookup is read with Mutagen when available, rather
than trusting fpcalc's demuxer estimate. A reproduced Jumbotron Shit Poppin case
had 147.50s from fpcalc versus 137.15s from Mutagen: the same fingerprint returned
zero results at 147s and a 0.9993407 match at 137s. This does not change the audio.
Rebuild and downloads share the same duration/fingerprint path.

An fpcalc decoder error is reported with its exit code and diagnostic text,
not attributed to a missing executable. A partial fingerprint emitted on a
nonzero exit is not submitted. Client diagnostics use the application's logger
so corrected durations, lookup scores and decoder failures appear in Live Logs.
