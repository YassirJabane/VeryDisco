"""Regression coverage for useful audio-feature tags, not feature inference."""
import io
import struct

import pytest
from mutagen.flac import FLAC
from mutagen.id3 import ID3, TBPM, TMOO
from mutagen.mp4 import MP4FreeForm, MP4Tags
from mutagen.mp4._atom import Atoms

from backend.app.metadata_pipeline.extras import normalize_extras, read_extras, write_extras
from backend.app.metadata_pipeline.tags import read_track, write_track
from backend.app.sync import embed_metadata


def test_post_download_wipe_preserves_bpm_and_multiple_moods(tmp_path):
    path = tmp_path / 'track.mp3'
    tags = ID3()
    tags.add(TBPM(encoding=3, text=['123.5']))
    tags.add(TMOO(encoding=3, text=['Relaxed', 'Melancholic']))
    tags.save(path)
    embed_metadata(str(path), 'Artist', 'Track', 'Album')
    assert ID3(path)['TBPM'].text == ['123.5']
    assert ID3(path)['TMOO'].text == ['Relaxed', 'Melancholic']


def test_explicit_features_written_and_rebuild_snapshot_can_restore_them(tmp_path):
    path = tmp_path / 'track.mp3'
    ID3().save(path)
    embed_metadata(str(path), 'Artist', 'Track', 'Album',
                   metadata_extras={'bpm': 120, 'mood': ['Happy', 'Energetic']})
    snapshot = read_track(path, include_cover=True)
    assert snapshot['bpm'] == ['120']
    assert snapshot['mood'] == ['Happy', 'Energetic']
    write_track(path, {**snapshot, 'bpm': '90', 'mood': ['Relaxed']})
    write_track(path, snapshot)
    assert read_track(path)['bpm'] == snapshot['bpm']
    assert read_track(path)['mood'] == snapshot['mood']


@pytest.mark.parametrize('bpm', ['NaN', 'Infinity', '-1', 0, True, 'unknown', '1e-99999', 65536, ['90', '120']])
def test_invalid_provider_bpm_does_not_erase_source_tempo(tmp_path, bpm):
    path = tmp_path / 'track.mp3'
    tags = ID3()
    tags.add(TBPM(encoding=3, text=['120']))
    tags.save(path)
    embed_metadata(str(path), 'Artist', 'Track', 'Album', metadata_extras={'bpm': bpm})
    assert read_extras(path)['bpm'] == ['120']


def test_absent_features_are_not_invented_and_explicit_delete_works(tmp_path):
    path = tmp_path / 'track.mp3'
    ID3().save(path)
    embed_metadata(str(path), 'Artist', 'Track', 'Album')
    assert read_extras(path)['bpm'] == read_extras(path)['mood'] == []
    write_extras(path, {'bpm': 120, 'mood': 'Happy'})
    write_extras(path, {'bpm': [], 'mood': []})
    assert 'TBPM' not in ID3(path) and 'TMOO' not in ID3(path)


def test_flac_features_survive_full_wipe(tmp_path):
    path = tmp_path / 'track.flac'
    # STREAMINFO metadata fixture: valid headers, no real audio payload needed.
    stream_info = struct.pack('>HH', 4096, 4096) + b'\0' * 6
    stream_info += ((44100 << 44) | (15 << 36) | 4410).to_bytes(8, 'big') + b'\0' * 16
    path.write_bytes(b'fLaC\x80\x00\x00\x22' + stream_info)
    audio = FLAC(path)
    audio['BPM'] = ['123.5']
    audio['MOOD'] = ['Relaxed', 'Sad']
    audio.save()
    embed_metadata(str(path), 'Artist', 'Track', 'Album')
    assert FLAC(path)['bpm'] == ['123.5']
    assert FLAC(path)['mood'] == ['Relaxed', 'Sad']


def test_mp4_serializes_integer_tempo_and_utf8_mood_and_reads_case_alias(tmp_path, monkeypatch):
    # Exercise Mutagen's real MP4 atom serializer on a metadata-only container;
    # substituting File avoids pretending this fixture contains playable audio.
    def atom(name, data=b''):
        return struct.pack('>I4s', 8 + len(data), name) + data

    stream = io.BytesIO(atom(b'ftyp', b'isom\0\0\0\0isom') + atom(b'moov'))
    class TagFile(MP4Tags):
        def save(self):
            MP4Tags.save(self, stream)

    tags = TagFile()
    tags['----:com.apple.iTunes:mood'] = [MP4FreeForm('Rêveur'.encode())]
    tags['tmpo'] = [120]
    tags.save()
    monkeypatch.setattr('mutagen.File', lambda path: tags)
    path = tmp_path / 'track.m4a'
    assert read_extras(path)['mood'] == ['Rêveur']
    write_extras(path, {'bpm': '123.5', 'mood': ['Happy', 'Énergique']})
    stream.seek(0)
    loaded = MP4Tags(Atoms(stream), stream)
    assert loaded['tmpo'] == [124]  # tmpo only supports unsigned integers.
    assert [bytes(v).decode() for v in loaded['----:com.apple.iTunes:MOOD']] == ['Happy', 'Énergique']
    assert '----:com.apple.iTunes:mood' not in loaded
    write_extras(path, {'bpm': [], 'mood': []})
    stream.seek(0)
    loaded = MP4Tags(Atoms(stream), stream)
    assert 'tmpo' not in loaded and '----:com.apple.iTunes:MOOD' not in loaded


def test_bpm_validation_retains_fractional_precision_for_text_tags():
    assert normalize_extras({'bpm': ' 123.500 '}) == {'bpm': ['123.5']}
    assert normalize_extras({'bpm': None, 'mood': 'Happy'}) == {'bpm': [], 'mood': 'Happy'}
