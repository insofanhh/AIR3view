import pytest

from backend import batches, media, store, youtube_auth
from backend.app import app
from fastapi.testclient import TestClient


@pytest.fixture
def local_data(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'DB', tmp_path / 'studio.sqlite3')
    return tmp_path


def test_auth_is_opt_in_and_cookie_file_is_local(local_data):
    assert youtube_auth.options() == {}
    assert youtube_auth.status() == {'mode': 'none', 'has_cookie_file': False}
    with pytest.raises(ValueError, match='tải lên'):
        youtube_auth.set_mode('file')
    with pytest.raises(ValueError, match='Netscape'):
        youtube_auth.save_cookie_file(b'not a cookie file')
    cookies = b'# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tsecret\n'
    assert youtube_auth.save_cookie_file(cookies)['mode'] == 'file'
    assert youtube_auth.options() == {'cookiefile': str(local_data / '_youtube' / 'cookies.txt')}
    assert (local_data / '_youtube' / 'cookies.txt').read_bytes().splitlines() == cookies.splitlines()
    assert 'secret' not in str(youtube_auth.status())
    youtube_auth.set_mode('chrome')
    assert youtube_auth.options() == {'cookiesfrombrowser': ('chrome',)}


def test_auth_api_validates_upload_and_does_not_return_cookie(local_data):
    client = TestClient(app)
    headers = {'X-AIR3view': 'studio'}
    assert client.get('/api/youtube/auth').json()['mode'] == 'none'
    assert client.put('/api/youtube/auth', json={'mode': 'file'}, headers=headers).status_code == 422
    assert client.post('/api/youtube/cookies', files={'file': ('cookies.txt', b'invalid')},
                       headers=headers).status_code == 422
    cookies = b'# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tsecret\n'
    response = client.post('/api/youtube/cookies', files={'file': ('cookies.txt', cookies)}, headers=headers)
    assert response.status_code == 200
    assert response.json() == {'mode': 'file', 'has_cookie_file': True}
    assert 'secret' not in response.text


def test_changing_auth_retries_caption_discovery(local_data, monkeypatch):
    import yt_dlp

    seen = []

    class FakeYoutubeDL:
        def __init__(self, options):
            seen.append(options)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def extract_info(self, *_args, **_kwargs):
            return {'subtitles': {}, 'automatic_captions': {}}

    monkeypatch.setattr(yt_dlp, 'YoutubeDL', FakeYoutubeDL)
    project = {'id': 'a' * 32, 'source': {'kind': 'youtube', 'url': 'https://youtu.be/EXiQCyqxmSE'},
               'metadata': {'duration': 30}, 'transcript': [], 'warnings': []}
    assert not media.try_source_subtitles(project)
    assert not media.try_source_subtitles(project)
    assert len(seen) == 1
    youtube_auth.set_mode('edge')
    assert not media.try_source_subtitles(project)
    assert len(seen) == 2
    assert seen[-1]['cookiesfrombrowser'] == ('edge',)


def test_bot_challenge_is_actionable_and_not_batch_retried(local_data, monkeypatch):
    import yt_dlp

    youtube_auth.set_mode('chrome')

    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options
            assert options['cookiesfrombrowser'] == ('chrome',)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def extract_info(self, *_args, **_kwargs):
            raise RuntimeError("Sign in to confirm you're not a bot. Use --cookies-from-browser")

    monkeypatch.setattr(yt_dlp, 'YoutubeDL', FakeYoutubeDL)
    project = {'id': 'a' * 32, 'source': {'kind': 'youtube', 'url': 'https://youtu.be/EXiQCyqxmSE', 'file': ''}}
    with pytest.raises(RuntimeError, match='YouTube yêu cầu xác minh phiên tải') as error:
        media.prepare(project, lambda *_: None, lambda: None)
    assert not batches.retryable_error(str(error.value))


def test_locked_chrome_cookie_error_is_actionable(local_data):
    youtube_auth.set_mode('chrome')
    error = youtube_auth.friendly_error('ERROR: Could not copy Chrome cookie database')
    assert 'cookies.txt' in error
    assert not batches.retryable_error(error)


def test_youtube_runtime_uses_local_deno_and_reports_network_denial(local_data, monkeypatch):
    runtime = local_data / '_runtime' / 'deno.exe'
    runtime.parent.mkdir()
    runtime.touch()
    monkeypatch.setattr(youtube_auth, '_runtime_version', lambda path: 'deno 2.9.7')
    assert youtube_auth.runtime_options() == {'js_runtimes': {'deno': {'path': str(runtime)}}}
    message = youtube_auth.friendly_error('Failed to establish a new connection: [WinError 10013]')
    assert 'cookie không gây ra lỗi mạng' in message
    assert not batches.retryable_error(message)


def test_missing_javascript_runtime_explains_missing_formats(local_data, monkeypatch):
    monkeypatch.setattr(youtube_auth.shutil, 'which', lambda _: None)
    message = youtube_auth.friendly_error('Requested format is not available')
    assert 'thiếu Deno' in message
