import json
import os
import subprocess
import sys

import pytest

from backend import credentials, providers, store


@pytest.fixture
def credential_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(credentials, '_protect', lambda value: b'protected:' + value.encode())
    monkeypatch.setattr(credentials, '_unprotect', lambda value: value.removeprefix(b'protected:').decode())
    monkeypatch.setattr(providers, 'SESSION_KEY', '')
    monkeypatch.setattr(providers, 'GEMINI_SESSION_KEY', '')
    for name in ('OPENAI_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    return tmp_path / '.private' / 'credentials.json'


def test_keys_are_encrypted_and_isolated_by_provider(credential_store):
    credentials.set_key('openai', ' sk-openai-secret ')
    credentials.set_key('gemini', 'gemini-secret')

    raw = credential_store.read_text('utf-8')
    payload = json.loads(raw)
    assert 'sk-openai-secret' not in raw and 'gemini-secret' not in raw
    assert set(payload['keys']) == {'openai', 'gemini'}
    assert credentials.get_key('openai') == 'sk-openai-secret'
    assert credentials.get_key('gemini') == 'gemini-secret'

    credentials.delete_key('openai')
    assert credentials.get_key('openai') == ''
    assert credentials.get_key('gemini') == 'gemini-secret'


def test_empty_value_deletes_last_key_and_file(credential_store):
    credentials.set_key('openai', 'secret')
    credentials.set_key('openai', '   ')
    assert credentials.get_key('openai') == ''
    assert not credential_store.exists()


def test_provider_lookup_precedence_and_persistent_redaction(credential_store, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'environment-secret')
    assert providers.key_source() == 'environment'
    credentials.set_key('openai', 'persistent-secret')
    assert providers.key() == 'persistent-secret'
    assert providers.key_source() == 'local_encrypted'
    monkeypatch.setattr(providers, 'SESSION_KEY', 'session-secret')
    assert providers.key() == 'session-secret'
    assert providers.key_source() == 'session'
    assert providers.redact('session-secret persistent-secret environment-secret') == '[KEY] [KEY] [KEY]'


def test_session_copy_of_persisted_value_reports_encrypted_storage(credential_store, monkeypatch):
    credentials.set_key('gemini', 'same-secret')
    monkeypatch.setattr(providers, 'GEMINI_SESSION_KEY', 'same-secret')
    assert providers.key_source('gemini') == 'local_encrypted'


def test_invalid_provider_and_corrupt_store_fail_without_plaintext_fallback(credential_store):
    with pytest.raises(ValueError):
        credentials.set_key('other', 'secret')
    credential_store.parent.mkdir(parents=True)
    credential_store.write_text('{broken', encoding='utf-8')
    with pytest.raises(RuntimeError, match='mã hóa'):
        credentials.get_key('openai')

    credential_store.write_text('[]', encoding='utf-8')
    with pytest.raises(RuntimeError, match='Định dạng'):
        credentials.get_key('openai')


def test_write_os_error_is_sanitized(credential_store, monkeypatch):
    monkeypatch.setattr(credentials, '_write_unchecked', lambda payload: (_ for _ in ()).throw(OSError('sensitive path')))
    with pytest.raises(RuntimeError, match='Không thể lưu kho API key') as error:
        credentials.set_key('openai', 'secret')
    assert 'sensitive path' not in str(error.value)


def test_unreadable_persistent_key_does_not_break_environment_fallback(credential_store, monkeypatch):
    credential_store.parent.mkdir(parents=True)
    credential_store.write_text('{broken', encoding='utf-8')
    monkeypatch.setenv('OPENAI_API_KEY', 'environment-secret')
    assert providers.key() == 'environment-secret'
    assert providers.key_source() == 'environment'
    assert providers.redact('environment-secret') == '[KEY]'


def test_non_windows_refuses_plaintext_storage(tmp_path, monkeypatch):
    if sys.platform == 'win32':
        pytest.skip('Non-Windows behavior')
    monkeypatch.setattr(store, 'DATA', tmp_path)
    with pytest.raises(RuntimeError, match='Windows'):
        credentials.set_key('openai', 'secret')
    assert not (tmp_path / '.private' / 'credentials.json').exists()


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows DPAPI only')
def test_windows_dpapi_round_trip_does_not_embed_plaintext():
    secret = 'air3view-dpapi-roundtrip'
    protected = credentials._protect(secret)
    assert secret.encode() not in protected
    assert credentials._unprotect(protected) == secret


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows DPAPI only')
def test_windows_key_survives_process_restart(tmp_path):
    synthetic = 'sk-air3view-restart-test-only'
    environment = os.environ.copy()
    environment['AIR3VIEW_DATA'] = str(tmp_path)
    environment['AIR3VIEW_TEST_KEY'] = synthetic
    for name in ('OPENAI_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_API_KEY'):
        environment.pop(name, None)

    save = subprocess.run(
        [sys.executable, '-c',
         "import os; from backend import credentials; credentials.set_key('openai', os.environ['AIR3VIEW_TEST_KEY']); print('saved')"],
        cwd=store.ROOT, env=environment, capture_output=True, text=True, check=True,
    )
    assert save.stdout.strip() == 'saved'

    load = subprocess.run(
        [sys.executable, '-c',
         "import os; from backend import providers; print(providers.key() == os.environ['AIR3VIEW_TEST_KEY'])"],
        cwd=store.ROOT, env=environment, capture_output=True, text=True, check=True,
    )
    assert load.stdout.strip() == 'True'
