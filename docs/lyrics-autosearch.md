# Lyrics autosearch and initial-download aliases

Lyrics Manager's **Autosearch all** starts a background pass over the signed-in
user's indexed tracks without synced lyrics. This includes missing and plain
lyrics, regardless of the current UI filter. Refresh Scan first if the library
index is stale. This is an explicit bulk action, not a scheduled recurring job.

Missing tracks can receive plain or synced lyrics; existing plain lyrics are
only upgraded to synced. Synced sidecars, instrumentals and files outside the
user's music directory are skipped. Automatic matches require matching artist
and title, and a duration within five seconds when both durations are available.
Existing manual edits made during a lookup win. Sidecars are saved atomically;
audio files and tags are not changed. The index is refreshed incrementally and
Navidrome is notified once after the batch.

LRCLIB calls are serialized, spaced at least one second apart and have an
explicit timeout. HTTP 429 stops the bulk pass and establishes a shared cooldown
of at least 60 seconds, respecting numeric Retry-After values. Per-track failures
are counted separately from no match. One job per user is allowed; the UI polls
progress and reconnects on reopening. Jobs/progress are in memory and do not
survive an application restart. Running Tasks can cancel an active pass.

Album, single-track and playlist download searches now resolve MusicBrainz
artist aliases lazily after the first query produces no usable candidates,
including on the first download before recording metadata exists. Alias queries
precede the remaining broad fallbacks. Ambiguous artist identities are ignored.
The operation-local alias context caches lookups across an album's songs and
fallbacks, and is discarded at the end; aliases are not persisted. Matching
retains its artist/version/quality checks, and legal/search-hint aliases alone
do not establish artist identity.
