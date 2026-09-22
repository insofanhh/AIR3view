import json
from pathlib import Path

import pytest

from backend import media


def project(tmp_path):
    return {
        'id': 'a' * 32,
        'source': {'kind': 'youtube', 'url': 'https://youtu.be/example'},
        'metadata': {'duration': 20},
        'transcript': [],
        'warnings': [],
    }


class FakeYoutubeDL:
    info = {}
    payload = ''
    options = []

    def __init__(self, options):
        self.options = options
        type(self).options.append(options)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, download=False):
        if not download:
            return type(self).info
        output = self.options['outtmpl'].replace('%(ext)s', self.options['subtitlesformat'])
        Path(output).write_text(type(self).payload, encoding='utf-8')
        return type(self).info


@pytest.fixture
def fake_ytdlp(monkeypatch, tmp_path):
    import yt_dlp

    FakeYoutubeDL.options = []
    monkeypatch.setattr(yt_dlp, 'YoutubeDL', FakeYoutubeDL)
    monkeypatch.setattr(media.store, 'project_dir', lambda _pid: tmp_path)
    return FakeYoutubeDL


def test_human_vtt_is_preferred_and_validated(fake_ytdlp):
    fake_ytdlp.info = {
        'language': 'en', 'duration': 20,
        'subtitles': {'en': [{'ext': 'vtt'}]},
        'automatic_captions': {'en': [{'ext': 'json3'}]},
    }
    fake_ytdlp.payload = '''WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nHello there\n'''
    p = project(None)

    assert media.try_source_subtitles(p)
    assert p['transcript_origin'] == 'youtube_subtitles'
    assert p['transcript_language'] == 'English'
    assert p['transcript'][0]['text'] == 'Hello there'
    assert fake_ytdlp.options[1]['subtitleslangs'] == ['en']
    assert fake_ytdlp.options[1]['subtitlesformat'] == 'vtt'


def test_auto_json3_rolling_captions_are_collapsed(fake_ytdlp):
    fake_ytdlp.info = {
        'original_language': 'vi', 'duration': 20,
        'subtitles': {},
        'automatic_captions': {'vi': [{'ext': 'json3'}]},
    }
    fake_ytdlp.payload = json.dumps({'events': [
        {'tStartMs': 1000, 'dDurationMs': 1800, 'segs': [{'utf8': 'Xin'}]},
        {'tStartMs': 1050, 'dDurationMs': 1900, 'segs': [{'utf8': 'Xin chào'}]},
        {'tStartMs': 5000, 'dDurationMs': 1200, 'segs': [{'utf8': 'Bạn khỏe không'}]},
    ]})
    p = project(None)

    assert media.try_source_subtitles(p)
    assert p['transcript_origin'] == 'youtube_auto_subtitles'
    assert p['transcript_language'] == 'Vietnamese'
    assert [cue['text'] for cue in p['transcript']] == ['Xin chào', 'Bạn khỏe không']


def test_marker_prevents_repeated_network_attempt_and_existing_transcript_wins(fake_ytdlp):
    fake_ytdlp.info = {'subtitles': {}, 'automatic_captions': {}}
    p = project(None)
    assert not media.try_source_subtitles(p)
    assert len(fake_ytdlp.options) == 1
    assert not media.try_source_subtitles(p)
    assert len(fake_ytdlp.options) == 1

    p['transcript'] = [{'id': 'manual', 'start': 0, 'end': 1, 'text': 'Manual'}]
    assert media.try_source_subtitles(p)
    assert p['transcript'][0]['text'] == 'Manual'


def test_existing_source_transcript_is_preserved_without_fetch(fake_ytdlp):
    p = project(None)
    p['source_transcript'] = [{'id': 'source', 'start': 1, 'end': 2, 'text': 'Keep me'}]

    assert media.try_source_subtitles(p)
    assert p['transcript'][0]['text'] == 'Keep me'
    assert fake_ytdlp.options == []


def test_music_only_track_is_not_meaningful_coverage(fake_ytdlp):
    fake_ytdlp.info = {'duration': 20, 'language': 'en',
                       'subtitles': {'en': [{'ext': 'vtt'}]}}
    fake_ytdlp.payload = 'WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n[Music] background\n'
    p = project(None)

    assert not media.try_source_subtitles(p)
    assert p['transcript'] == []


def test_bad_timestamps_fall_back_without_installing_caption(fake_ytdlp):
    fake_ytdlp.info = {'duration': 20, 'language': 'en',
                       'subtitles': {'en': [{'ext': 'vtt'}]}}
    fake_ytdlp.payload = 'WEBVTT\n\n00:00:30.000 --> 00:00:31.000\nToo late\n'
    p = project(None)

    assert not media.try_source_subtitles(p)
    assert p['transcript'] == []
    assert any('hợp lệ' in warning for warning in p['warnings'])


def test_cancellation_is_not_swallowed(fake_ytdlp):
    fake_ytdlp.info = {'subtitles': {}, 'automatic_captions': {}}
    p = project(None)

    def cancel():
        raise media.Cancelled('stop')

    with pytest.raises(media.Cancelled):
        media.try_source_subtitles(p, check=cancel)
    assert 'source_subtitles_check' not in p

    # Cancellation should not make a later retry look already attempted.
    assert not media.try_source_subtitles(p)
    assert len(fake_ytdlp.options) == 1
