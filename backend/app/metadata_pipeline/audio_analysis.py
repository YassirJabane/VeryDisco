"""Validated audio descriptors and optional, isolated Essentia analysis."""
import asyncio
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path


def descriptor(raw, path):
    if path in raw:
        value = raw[path]
    else:
        value = raw
        for part in path.split('.'):
            value = value.get(part) if isinstance(value, dict) else None
    return value.get('mean') if isinstance(value, dict) and 'mean' in value else value


def audio_features(raw):
    result = {}
    for name, path in {
        'bpm': 'rhythm.bpm', 'bpm_confidence': 'rhythm.bpm_confidence',
        'danceability': 'rhythm.danceability', 'onset_rate': 'rhythm.onset_rate',
        'bpm_first_peak': 'rhythm.bpm_histogram_first_peak_bpm',
        'bpm_second_peak': 'rhythm.bpm_histogram_second_peak_bpm',
        'dynamic_complexity': 'lowlevel.dynamic_complexity',
        'average_loudness': 'lowlevel.average_loudness',
        'integrated_loudness': 'lowlevel.loudness_ebu128.integrated',
    }.items():
        try:
            original = descriptor(raw, path)
            if isinstance(original, bool):
                continue
            value = float(original)
            if math.isfinite(value) and (name != 'bpm' or 1 <= value <= 400):
                result[name] = value
        except (TypeError, ValueError):
            pass
    for profile in ['key_edma', 'key_krumhansl', 'key_temperley', None]:
        prefix = f'tonal.{profile}.' if profile else 'tonal.key_'
        key, scale = descriptor(raw, prefix + 'key'), descriptor(raw, prefix + 'scale')
        if isinstance(key, str) and isinstance(scale, str) and key in {'A', 'A#', 'Bb', 'B', 'Cb', 'C', 'C#', 'Db', 'D', 'D#', 'Eb', 'E', 'Fb', 'F', 'E#', 'F#', 'Gb', 'G', 'G#', 'Ab', 'B#'} and scale in {'major', 'minor'}:
            result['key'] = f'{key} {scale}'
            canonical = {'Db': 'C#', 'Eb': 'D#', 'Gb': 'F#', 'Ab': 'G#', 'Bb': 'A#',
                         'Cb': 'B', 'Fb': 'E', 'E#': 'F', 'B#': 'C'}.get(key, key)
            wheels = {'minor': {'A': 8, 'A#': 3, 'B': 10, 'C': 5, 'C#': 12, 'D': 7,
                                'D#': 2, 'E': 9, 'F': 4, 'F#': 11, 'G': 6, 'G#': 1},
                      'major': {'A': 11, 'A#': 6, 'B': 1, 'C': 8, 'C#': 3, 'D': 10,
                                'D#': 5, 'E': 12, 'F': 7, 'F#': 2, 'G': 9, 'G#': 4}}
            result['camelot'] = f"{wheels[scale][canonical]}{'A' if scale == 'minor' else 'B'}"
            result['key_profile'] = profile or 'archive'
            try:
                strength = float(descriptor(raw, prefix + 'strength'))
                if math.isfinite(strength):
                    result['key_strength'] = strength
            except (TypeError, ValueError):
                pass
            break
    return result


def highlevel_features(raw):
    result = {}
    for name, item in (raw.get('highlevel') or {}).items():
        if not isinstance(item, dict) or not isinstance(item.get('value'), str):
            continue
        try:
            probability = float(item.get('probability'))
        except (TypeError, ValueError):
            continue
        if math.isfinite(probability) and 0 <= probability <= 1:
            result[name] = {'value': item['value'], 'probability': probability}
    return result


class LocalAudioAnalyzer:
    def __init__(self, cache_get=None, cache_set=None):
        self.cache_get, self.cache_set = cache_get, cache_set
        self.lock = asyncio.Lock()

    async def analyze(self, path, expected_size=None, expected_mtime_ns=None):
        path = Path(path)
        before = path.stat()
        signature = (before.st_size, before.st_mtime_ns)
        if expected_size is not None and signature != (expected_size, expected_mtime_ns):
            return {'status': 'file_changed'}
        identity = hashlib.sha256(f'{path.resolve()}\0{signature}\0essentia-v1'.encode()).hexdigest()
        cache_key = f'local-audio:{identity}'
        async with self.lock:
            cached = await self.cache_get(cache_key) if self.cache_get else None
            if cached:
                return cached
            if importlib.util.find_spec('essentia') is None:
                return {'status': 'not_installed', 'source': 'Essentia local'}
            process = await asyncio.create_subprocess_exec(
                sys.executable, '-m', 'backend.app.metadata_pipeline.audio_worker', str(path.resolve()),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=180)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                if process.returncode is None:
                    process.kill()
                    await process.communicate()
                raise
            after = path.stat()
            if signature != (after.st_size, after.st_mtime_ns):
                return {'status': 'file_changed'}
            if process.returncode != 0:
                return {'status': 'decoder_error', 'source': 'Essentia local'}
            try:
                features = audio_features(json.loads(stdout))
            except (ValueError, TypeError, AttributeError):
                return {'status': 'invalid_response', 'source': 'Essentia local'}
            result = {'status': 'found' if features else 'not_found', 'features': features,
                      'source': 'Essentia local', 'analysis_version': 'music-extractor-v1'}
            if features and self.cache_set:
                await self.cache_set(cache_key, result, 30 * 86400)
            return result
