from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend import updates
from backend.app import app


def release(version='1.2.0', uploaded=True):
    return {'tag_name': 'v' + version, 'html_url': updates.RELEASES_URL + '/tag/v' + version,
            'draft': False, 'prerelease': False,
            'assets': [{'name': f'AIR3view-Setup-{version}-win64.exe',
                        'state': 'uploaded' if uploaded else 'starter'}]}


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
