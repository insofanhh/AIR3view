import pytest
from fastapi.testclient import TestClient

from backend import credentials, providers, store
from backend.app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'DB', tmp_path / 'studio.sqlite3')
    monkeypatch.setattr(credentials, '_protect', lambda value: value[::-1].encode())
    monkeypatch.setattr(credentials, '_unprotect', lambda value: value.decode()[::-1])
    monkeypatch.setattr(providers, 'SESSION_KEY', '')
    monkeypatch.setattr(providers, 'GEMINI_SESSION_KEY', '')
    for name in ('OPENAI_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    store.init()
    return TestClient(app)


def test_clear_persisted_key_reports_environment_fallback(client, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-env-secret')
    headers = {'X-AIR3view': 'studio'}
    saved = client.post('/api/key', json={'provider': 'gemini', 'api_key': 'test-local-secret'}, headers=headers)
    assert saved.json()['source'] == 'local_encrypted'
    cleared = client.post('/api/key', json={'provider': 'gemini', 'api_key': ''}, headers=headers)
    assert cleared.json()['source'] == 'environment'
    assert cleared.json()['configured'] is True
    assert credentials.get_key('gemini') == ''
    assert providers.key('gemini') == 'test-env-secret'
    assert 'test-env-secret' not in client.get('/api/health').text


def test_failed_key_save_preserves_active_key(client, monkeypatch):
    credentials.set_key('openai', 'old-secret')

    def unavailable(*args):
        raise RuntimeError('Không thể lưu kho API key đã mã hóa.')

    monkeypatch.setattr(credentials, 'set_key', unavailable)
    response = client.post('/api/key', json={'api_key': 'new-secret'}, headers={'X-AIR3view': 'studio'})
    assert response.status_code == 422
    assert providers.key() == 'old-secret'
    assert 'new-secret' not in response.text


def test_credential_store_is_not_exposed_by_media_or_documents(client):
    credentials.set_key('openai', 'secret-not-in-project')
    project = store.create('Private', {'kind': 'upload', 'file': 'source.mp4'})
    for path in ('/media/.private/credentials.json', '/.private/credentials.json',
                 '/data/.private/credentials.json', '/media/'+project['id']+'/../.private/credentials.json'):
        assert client.get(path).status_code in (404, 422)
    for path in ('/api/health', '/api/preferences', '/api/projects',
                 '/api/projects/'+project['id']+'/document'):
        assert 'secret-not-in-project' not in client.get(path).text
