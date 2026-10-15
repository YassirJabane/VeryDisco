# BPM and mood: preservation, not inference

Subsequent update: `2026-10-15-bpm-rebuild-enrichment.md` documents the optional
recording-MBID BPM archive lookup now available in Metadata Rebuild. The original
preservation fix below does not itself perform that lookup or infer mood.

## Confirmed cause

The post-download writer completely wipes source metadata. Its useful-tag allowlist
did not include BPM or mood, so even populated source tags were lost. The existing
MusicBrainz extras resolver fetches genres, identifiers, labels and dates, not audio
tempo or mood. There is no audio-analysis stage in VeryDisco today.

This explains a code path that can leave these fields empty; no production-library
inventory was performed as part of this fix.

## Change

- Preserve and write BPM and multi-valued mood through the shared extras pipeline.
- Include them in Metadata Rebuild inspection snapshots/backups and restoration.
- MP3: `TBPM`, `TMOO`; FLAC/Ogg Vorbis: `BPM`, `MOOD`; MP4/M4A: integer `tmpo`,
  UTF-8 `----:com.apple.iTunes:MOOD`.
- Read MP4 mood freeform aliases case-insensitively and replace them with one
  canonical atom when explicitly writing or deleting mood.
- BPM must be a single finite numeric value between 1 and 65535. Invalid provider
  values do not replace a valid source tempo or fail the import. Text formats keep
  fractional BPM; MP4's unsigned integer atom rounds half up to the nearest BPM.
- Empty provider values do not erase source metadata. The explicit extras writer
  still supports deletion using empty values.

Mappings checked against Navidrome's official
[resources/mappings.yaml](https://github.com/navidrome/navidrome/blob/master/resources/mappings.yaml).

## Application and limits

Deploy the updated beta before subsequent imports or retagging operations.
Existing populated tags are retained when retagged. Navidrome must rescan files
after any actual tag update; a scan alone cannot create absent tags.

No historical files are automatically changed, no albums are re-downloaded, and
no audio analysis or external service is enabled. Values already stripped from
the library cannot be reconstructed by this patch. Filling them requires original
tagged files, a verified recording-level source, or a separately designed audio
analysis/enrichment pipeline with provenance and confidence handling. In particular,
mood must not be manufactured from genre or an artist-level label.

## Verification

`backend/tests/test_bpm_mood_tags.py` covers MP3 full-wipe preservation, explicit
feature writes, metadata snapshot restoration, invalid BPM inputs, absence/deletion,
FLAC full-wipe preservation, and real Mutagen MP4 tag serialization on a
metadata-only container. These fixtures test tags, not the quality of audio analysis
or a live Navidrome deployment.
