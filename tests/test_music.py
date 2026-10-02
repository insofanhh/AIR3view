import copy
import io
import math
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend import batches, music, preferences, render_cache, store
from backend.app import app
from backend.models import Settings
from backend.providers import voice_hash
from backend.story import plan_fingerprint
from backend.timeline import build

HEADERS = {'X-AIR3view': 'studio'}
URL_A = 'https://www.youtube.com/watch?v=EXiQCyqxmSE'
URL_B = 'https://youtu.be/WjCVzhRMsAo'


def wav_bytes(seconds=1.5, frequency=440, alternating=False):
    times = np.arange(round(seconds * 48000)) / 48000
    frequencies = np.where(times < seconds / 2, frequency, 660) if alternating else frequency
    samples = (np.sin(2 * math.pi * times * frequencies) * 6000).astype('<i2')
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(48000)
        writer.writeframes(samples.tobytes())
    return buffer.getvalue()


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'DB', tmp_path / 'studio.sqlite3')
    store.init()
    return tmp_path


def upload(client, route):
    response = client.post(route, files={'file': ('Nhạc nền.wav', wav_bytes(), 'audio/wav')}, headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def test_single_music_upload_timeline_remove_and_undo(database):
    client = TestClient(app)
    p = store.create('Music', {'kind': 'upload', 'file': 'source.mp4'})
    p['metadata']['duration'] = 8
    store.save(p)
    route = f'/api/projects/{p["id"]}'
    p = upload(client, route + '/music')
    asset = p['settings']['music_file']
    assert music.project_asset(p).read_bytes() == wav_bytes()
    assert p['settings']['music_duration'] == pytest.approx(1.5)
    assert p['settings']['music_name'] == 'Nhạc nền.wav'
    timeline = client.get(route + '/timeline').json()
    assert timeline['music']['end'] == 8
    assert timeline['music']['loop'] is True
    assert client.get(f'/media/{p["id"]}/{asset}').status_code == 200
    original = copy.deepcopy(p['settings'])
    def update(settings):
        body = {key: p[key] for key in ('revision', 'name', 'settings', 'transcript', 'narrations')}
        body['settings'] = settings
        response = client.put(route, json=body, headers=HEADERS)
        assert response.status_code == 200, response.text
        return response.json()
    p = update({**original, 'music_file': ''})
    assert client.get(route + '/timeline').json()['music'] is None
    p = update(original)
    assert p['settings']['music_file'] == asset
    assert client.get(route + '/timeline').json()['music']['audio'] == asset


def test_invalid_music_is_rejected_without_changing_project(database, monkeypatch):
    client = TestClient(app)
    p = store.create('Music', {'kind': 'upload', 'file': 'source.mp4'})
    route = f'/api/projects/{p["id"]}/music'
    for name, content in [('fake.mp3', b'not audio'), ('bad.txt', wav_bytes()), ('empty.wav', b'')]:
        response = client.post(route, files={'file': (name, content)}, headers=HEADERS)
        assert response.status_code == 422, response.text
    assert not list(store.project_dir(p['id']).glob('music-*'))
    assert not store.read(p['id'])['settings']['music_file']
    monkeypatch.setattr(music, 'MAX_BYTES', 10)
    assert client.post(route, files={'file': ('big.wav', wav_bytes())}, headers=HEADERS).status_code == 422
    for value in ('../music.wav', r'C:\music.wav', 'nested/music.wav'):
        with pytest.raises(ValueError):
            Settings(music_file=value)
    with pytest.raises(ValueError):
        music.staged('../music.wav')


def test_batch_music_is_copied_independently_with_volume_overrides(database):
    client = TestClient(app)
    staged = upload(client, '/api/batches/music')
    assert client.get('/api/batches/music/' + staged['token']).status_code == 200
    response = client.post('/api/batches', json={'items': [{'url': URL_A}, {'url': URL_B, 'settings': {'music_volume': .3}}],
        'settings': Settings(output_mode='single', music_volume=.1).model_dump(), 'music_token': staged['token']}, headers=HEADERS)
    assert response.status_code == 200, response.text
    projects = [store.read(item['project_id']) for item in response.json()['items']]
    assert [p['settings']['music_volume'] for p in projects] == [.1, .3]
    paths = [music.project_asset(p) for p in projects]
    assert paths[0] != paths[1] and all(path.read_bytes() == wav_bytes() for path in paths)
    paths[0].unlink()
    assert paths[1].is_file()
    source = projects[1]
    copied = batches.create('Profile', [{'url': URL_A}], source['settings'], source['id'])
    p = store.read(copied['items'][0]['project_id'])
    assert music.project_asset(p).read_bytes() == wav_bytes()
    with pytest.raises(ValueError, match='dự án nguồn'):
        batches.create('Unsafe', [{'url': URL_A}], source['settings'])
    with pytest.raises(ValueError, match='nhạc nền'):
        batches.create('Unsafe', [{'url': URL_A, 'settings': {'music_file': 'a.wav'}}], Settings().model_dump())


def test_music_changes_only_audio_identity_and_not_editorial_or_tts(database):
    p = store.create('Music', {'kind': 'upload', 'file': 'source.mp4'})
    folder = store.project_dir(p['id'])
    (folder / 'source.mp4').write_bytes(b'source identity')
    p['metadata'].update(duration=5, has_audio=True)
    plan = plan_fingerprint(p)
    narration = {'text': 'One voice', 'id': 'n1'}
    voice = voice_hash(narration, p['settings'])
    timeline = build(p)
    part = timeline['parts'][0]
    old_audio = render_cache.audio_key(p, timeline, part)
    _, metadata = music.save_upload(folder, 'music.wav', wav_bytes())
    p['settings'].update(metadata, music_volume=.2)
    assert plan_fingerprint(p) == plan
    assert voice_hash(narration, p['settings']) == voice
    assert render_cache.audio_key(p, timeline, part) != old_audio
    shared = preferences._shared(p['settings'])
    assert not music.ASSET_FIELDS.intersection(shared)
    assert shared['music_volume'] == .2
    a = render_cache.audio_key(p, timeline, part)
    p['settings']['music_volume'] = .4
    assert render_cache.audio_key(p, timeline, part) != a
    p['settings']['music_volume'] = 0
    assert render_cache.audio_key(p, timeline, part) == old_audio


def test_native_music_loops_through_muted_source_and_preserves_split_phase(database):
    from backend.media import probe
    p = store.create('Music', {'kind': 'upload', 'file': 'source.wav'})
    folder = store.project_dir(p['id'])
    (folder / 'source.wav').write_bytes(wav_bytes(5, 220))
    p['metadata'] = probe(folder / 'source.wav')
    _, metadata = music.save_upload(folder, 'music.wav', wav_bytes(alternating=True))
    p['settings'].update(metadata, music_volume=.3)
    timeline = dict(clips=[dict(kind='original', start=0, end=4, source_start=0, source_end=4)],
                    voices=[], source_mutes=[dict(start=0, end=4)], original_audio=[])
    def mix(start, end, name):
        destination = folder / name
        destination.mkdir()
        render_cache.mix_audio(p, timeline, dict(start=start, end=end, duration=end-start), destination, lambda: None, lambda *a: None)
        with wave.open(str(destination / 'mix.wav')) as reader:
            assert reader.getnframes() == round((end-start)*48000)
            return np.frombuffer(reader.readframes(reader.getnframes()), dtype='<i2').reshape(-1, 2)[:, 0].astype(float)
    full = mix(0, 4, 'full')
    left = mix(0, 2, 'left')
    right = mix(2, 4, 'right')
    assert np.max(np.abs(left[1000:-1000] - full[1000:2*48000-1000])) <= 2
    assert np.max(np.abs(right[1000:-1000] - full[2*48000+1000:-1000])) <= 2
    def amplitude(samples, frequency, start):
        y = samples[round(start*48000):round((start+.15)*48000)]
        return abs(np.sum(y*np.exp(-2j*np.pi*frequency*np.arange(len(y))/48000))) / len(y)
    assert amplitude(full, 440, .2) > 500
    assert amplitude(full, 660, .9) > 500
    assert amplitude(full, 440, 3.2) > 500  # third repetition
    assert amplitude(full, 220, .2) < 3  # source remains completely muted
    p['settings']['music_volume'] = .15
    quiet = mix(0, 4, 'quiet')
    assert amplitude(quiet, 440, .2) / amplitude(full, 440, .2) == pytest.approx(.5, abs=.005)
