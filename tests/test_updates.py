import hashlib
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend import updates
from backend.app import app


def release(version='1.2.0', uploaded=True):
    return {'tag_name': 'v' + version, 'html_url': updates.RELEASES_URL + '/tag/v' + version,
            'draft': False, 'prerelease': False,
            'assets': [{'name': f'AIR3view-Setup-{version}-win64.exe',
                        'state': 'uploaded' if uploaded else 'starter',
                        'size': 7, 'digest': 'sha256:' + 'a' * 64}]}


def test_only_newer_published_windows_installer_is_announced():
    assert updates._published_release(release('1.2.0'), '1.1.9')['available']
    assert not updates._published_release(release('1.2.0'), '1.2.0')['available']
    assert not updates._published_release(release('1.2.0'), '1.3.0')['available']
    for invalid in (release('1.2.0', False), {**release(), 'prerelease': True},
                    {**release(), 'html_url': 'https://example.com/fake'}, []):
        try:
            updates._published_release(invalid, '1.0.0')
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid release must not be shown')


def test_update_endpoint_caches_success_and_allows_manual_refresh(monkeypatch, tmp_path):
    version_file = tmp_path / 'APP_VERSION'
    version_file.write_text('1.0.0', encoding='utf-8')
    monkeypatch.setattr(updates, 'VERSION_FILE', version_file)
    monkeypatch.setattr(updates, '_cache', {'until': 0.0, 'value': None})
    calls = []

    def get(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: release())

    monkeypatch.setattr(updates.requests, 'get', get)
    client = TestClient(app)
    first = client.get('/api/update').json()
    assert first['available'] and first['latest_version'] == '1.2.0'
    assert first['release_url'] == updates.RELEASES_URL + '/tag/v1.2.0'
    assert client.get('/api/update').json() == first
    assert len(calls) == 1
    assert client.get('/api/update?refresh=true').json() == first
    assert len(calls) == 2


def test_offline_check_is_nonblocking_and_cached(monkeypatch):
    monkeypatch.setattr(updates, '_cache', {'until': 0.0, 'value': None})
    calls = []

    def get(*args, **kwargs):
        calls.append(1)
        raise updates.requests.Timeout('offline')

    monkeypatch.setattr(updates.requests, 'get', get)
    first = updates.check_updates()
    assert first['status'] == 'unavailable' and not first['available']
    assert updates.check_updates() == first
    assert len(calls) == 1


def test_download_verifies_release_digest_before_marking_ready(tmp_path, monkeypatch):
    from backend import store
    payload = b'installer-bytes'
    checksum = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(updates, 'install_supported', lambda: True)
    monkeypatch.setattr(updates, 'check_updates', lambda refresh=False: {
        'available': True, 'latest_version': '1.2.0', 'installer_size': len(payload),
        'installer_sha256': checksum})
    monkeypatch.setattr(updates, '_download_state', {'phase':'idle','version':'','downloaded':0,'total':0,'error':''})
    class Response:
        url = 'https://release-assets.githubusercontent.com/installer'
        status_code = 200
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, chunk_size): yield payload
    monkeypatch.setattr(updates.requests, 'get', lambda *args, **kwargs: Response())
    updates.start_download()
    deadline = time.monotonic() + 3
    while updates.download_status()['phase'] == 'downloading' and time.monotonic() < deadline:
        time.sleep(.01)
    assert updates.download_status()['phase'] == 'ready'
    assert (tmp_path / '_updates' / 'AIR3view-Setup-1.2.0-win64.exe').read_bytes() == payload


def test_download_rejects_checksum_mismatch(tmp_path, monkeypatch):
    from backend import store
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(updates, '_download_state', {'phase':'idle','version':'','downloaded':0,'total':0,'error':''})
    class Response:
        url = 'https://release-assets.githubusercontent.com/installer'
        status_code = 200
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, chunk_size): yield b'incorrect'
    monkeypatch.setattr(updates.requests, 'get', lambda *args, **kwargs: Response())
    updates._download('1.2.0', len(b'incorrect'), '0' * 64)
    assert updates.download_status()['phase'] == 'error'
    assert not list((tmp_path / '_updates').glob('*.exe'))


def test_mutations_stop_when_installer_handoff_begins():
    updates.INSTALL_GUARD.set()
    try:
        with TestClient(app) as client:
            response = client.post('/api/update/download', headers={'X-AIR3view':'studio'})
            assert response.status_code == 503
            assert client.get('/api/health').status_code == 200
    finally:
        updates.INSTALL_GUARD.clear()


def test_install_requires_idle_work_and_starts_external_helper(tmp_path, monkeypatch):
    from backend import store
    monkeypatch.setattr(store, 'DATA', tmp_path / 'data')
    store.DATA.mkdir()
    version_file = tmp_path / 'app' / 'APP_VERSION'
    version_file.parent.mkdir()
    version_file.write_text('1.1.0', 'utf-8')
    (version_file.parent / 'update-helper.ps1').write_text('test helper', 'utf-8')
    monkeypatch.setattr(updates, 'VERSION_FILE', version_file)
    monkeypatch.setattr(updates, 'install_supported', lambda: True)
    payload = b'checked installer'
    checksum = hashlib.sha256(payload).hexdigest()
    directory = store.DATA / '_updates'
    directory.mkdir()
    (directory / 'AIR3view-Setup-1.2.0-win64.exe').write_bytes(payload)
    monkeypatch.setattr(updates, '_download_state', {'phase':'ready','version':'1.2.0',
                       'downloaded':len(payload),'total':len(payload),'error':''})
    monkeypatch.setattr(updates, 'check_updates', lambda: {'available':True,
                       'latest_version':'1.2.0','installer_size':len(payload),
                       'installer_sha256':checksum})
    monkeypatch.setattr(updates, '_active_work', lambda: True)
    try:
        updates.start_install()
    except ValueError as error:
        assert 'Đang có video' in str(error)
    else:
        raise AssertionError('Must not install while video jobs are active')
    assert not updates.INSTALL_GUARD.is_set()
    monkeypatch.setattr(updates, '_active_work', lambda: False)
    calls = []
    monkeypatch.setattr(updates.subprocess, 'Popen', lambda *args, **kwargs: calls.append((args, kwargs)))
    updates.INSTALL_REQUESTED.clear()
    try:
        assert updates.start_install()['phase'] == 'installing'
        assert updates.INSTALL_REQUESTED.is_set()
        assert (directory / 'update-helper.ps1').is_file()
        assert calls[0][1]['env']['AIR3VIEW_UPDATE_VERSION'] == '1.2.0'
    finally:
        updates.INSTALL_REQUESTED.clear()
        updates.INSTALL_GUARD.clear()
