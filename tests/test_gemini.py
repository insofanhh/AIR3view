import base64
import json

import pytest
from fastapi.testclient import TestClient

from backend import credentials, gemini, providers, store
from backend.app import app
from backend.models import Settings, StoryAnswer, TranslationAnswer
from backend.providers import normalize_analysis_times


class Response:
    def __init__(self, data, status=200):
        self.data = data
        self.status_code = status
        self.ok = status < 400

    def json(self):
        return self.data


def generated(text):
    return Response({'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': text}]}}],
                     'usageMetadata': {'totalTokenCount': 12}})


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setattr(providers, 'SESSION_KEY', 'openai-test')
    monkeypatch.setattr(providers, 'GEMINI_SESSION_KEY', 'gemini-test')
    for name in ('OPENAI_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_API_KEY'):
        monkeypatch.delenv(name, raising=False)


def test_gemini_is_used_for_ai_workflow_and_key_is_not_in_body(tmp_path, monkeypatch, keys):
    image = tmp_path/'frame.png'
    image.write_bytes(b'frame')
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return generated('{"items":[{"id":"n1","text":"Hello"}]}')

    monkeypatch.setattr(gemini.requests, 'post', post)
    settings = Settings(provider='gemini', model='models/gemini-test').model_dump()
    result = providers.ask_ai('translate', [image], settings, tmp_path, lambda: None, TranslationAnswer)
    assert result['items'][0]['text'] == 'Hello'
    url, kwargs = calls[0]
    assert url.endswith('/models/gemini-test:generateContent')
    assert kwargs['headers'] == {'x-goog-api-key': 'gemini-test'}
    assert 'gemini-test' not in json.dumps(kwargs['json'])
    assert kwargs['json']['contents'][0]['parts'][1]['inlineData']['data'] == base64.b64encode(b'frame').decode()


def test_story_schema_is_inlined_and_compatible():
    schema = gemini.compatible_schema(StoryAnswer.model_json_schema())
    encoded = json.dumps(schema)
    assert '$defs' not in schema and '$ref' not in encoded
    assert schema['required'] == list(schema['properties'])
    assert schema['properties']['hook']['properties']['title']['type'] == 'string'
    assert 'title' not in schema
    assert 'title' not in schema['properties']['title']
    for unsupported in ('minLength', 'maxLength', 'minimum', 'maximum',
                        'exclusiveMinimum', 'minItems', 'maxItems'):
        assert unsupported not in encoded


def test_keys_are_isolated_and_redacted(monkeypatch, keys):
    assert providers.key() == 'openai-test'
    assert providers.key('gemini') == 'gemini-test'
    assert providers.redact('openai-test gemini-test AIza' + 'a'*30) == '[KEY] [KEY] [KEY]'


def test_model_catalog_filters_generate_content(monkeypatch):
    monkeypatch.setattr(gemini.requests, 'get', lambda *args, **kwargs: Response({'models': [
        {'name': 'models/gemini-a', 'displayName': 'A', 'supportedGenerationMethods': ['generateContent']},
        {'name': 'models/embed-a', 'supportedGenerationMethods': ['embedContent']},
    ]}))
    assert gemini.list_models('key') == [{'id': 'gemini-a', 'name': 'A', 'description': ''}]


def test_api_persists_gemini_key_and_exposes_catalog_without_secret(tmp_path, monkeypatch, keys):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'DB', tmp_path/'test.sqlite3')
    store.init()
    # Exercise API persistence independently of the OS codec (real DPAPI is
    # covered in test_credentials).
    monkeypatch.setattr(credentials, '_protect', lambda value: value[::-1].encode())
    monkeypatch.setattr(credentials, '_unprotect', lambda value: value.decode()[::-1])
    monkeypatch.setattr(gemini, 'list_models', lambda key: [{'id': 'model', 'name': 'Model', 'description': ''}] if key == 'new-gemini' else [])
    client = TestClient(app)
    headers = {'X-AIR3view': 'studio'}
    response = client.post('/api/key', headers=headers, json={'provider': 'gemini', 'api_key': ' new-gemini '})
    assert response.status_code == 200
    assert response.json()['storage'] == 'local_encrypted'
    assert response.json()['configured'] is True
    assert 'new-gemini' not in response.text
    assert providers.key() == 'openai-test' and providers.key('gemini') == 'new-gemini'
    assert client.get('/api/health').json()['gemini_api_key'] is True
    assert client.post('/api/gemini/models', headers=headers).json()['models'][0]['id'] == 'model'
    assert 'new-gemini' not in store.DB.read_bytes().decode('latin1')
    monkeypatch.setattr(providers, 'GEMINI_SESSION_KEY', '')
    assert providers.key('gemini') == 'new-gemini'
    assert client.get('/api/health').json()['gemini_key_source'] == 'local_encrypted'
    project = store.create('No secrets', {'kind': 'upload', 'file': 'source.mp4'})
    assert 'new-gemini' not in client.get('/api/projects/'+project['id']+'/document').text
    response = client.post('/api/key', headers=headers, json={'provider': 'gemini', 'api_key': ''})
    assert response.json()['configured'] is False
    assert providers.key('gemini') == ''
    assert providers.key('openai') == 'openai-test'


def test_gemini_provider_persists_with_project(tmp_path, monkeypatch, keys):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'DB', tmp_path/'test.sqlite3')
    store.init()
    project = store.create('Gemini', {'kind': 'upload', 'file': 'source.mp4'})
    body = {key: project[key] for key in ('revision', 'name', 'settings', 'narrations', 'transcript')}
    body['settings'].update(provider='gemini', model='gemini-test')
    response = TestClient(app).put('/api/projects/'+project['id'], headers={'X-AIR3view': 'studio'}, json=body)
    assert response.status_code == 200
    assert store.read(project['id'])['settings']['provider'] == 'gemini'


def analysis_result(scene_start, scene_end, narration_start, confidence=.95):
    return {'scenes': [{'start': scene_start, 'end': scene_end, 'description': 'Action',
                        'characters': [], 'evidence': 'frame', 'confidence': confidence}],
            'narrations': [{'start': narration_start, 'text': 'Narration', 'evidence': 'frame'}],
            'hooks': [{'start': narration_start, 'end': narration_start + 4, 'title': 'Hook', 'reason': 'Action'}],
            'summary': 'Summary', 'requires_insert': False}


def test_gemini_small_batch_boundary_overshoot_is_clipped():
    result = normalize_analysis_times(analysis_result(395.03, 421.26, 409), 360, 420)
    assert result['scenes'][0]['start'] == 395.03
    assert result['scenes'][0]['end'] == 420


def test_gemini_retries_invalid_structured_schema_in_json_mode(monkeypatch):
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs['json'])
        if len(calls) == 1:
            return Response({'error': {'status': 'INVALID_ARGUMENT', 'message': 'invalid schema'}}, 400)
        return generated('{"items":[{"id":"n1","text":"Hello"}]}')

    monkeypatch.setattr(gemini.requests, 'post', post)
    text, usage = gemini.generate('Translate', [], 'gemini-test',
                                  TranslationAnswer.model_json_schema(), 'gemini-key', lambda: None)
    assert json.loads(text)['items'][0]['text'] == 'Hello'
    assert 'responseJsonSchema' in calls[0]['generationConfig']
    assert 'responseJsonSchema' not in calls[1]['generationConfig']
    assert 'JSON schema' in calls[1]['contents'][0]['parts'][0]['text']
    assert usage['_air3view_schema_mode'] == 'json'


def test_gemini_relative_batch_times_are_shifted_to_absolute():
    result = normalize_analysis_times(analysis_result(2, 25, 5, 95), 360, 420)
    assert result['scenes'][0]['start'] == 362
    assert result['scenes'][0]['end'] == 385
    assert result['narrations'][0]['start'] == 365
    assert result['scenes'][0]['confidence'] == .95


def test_materially_wrong_gemini_timestamp_is_rejected():
    with pytest.raises(ValueError, match='ngoài đoạn'):
        normalize_analysis_times(analysis_result(395, 450, 409), 360, 420)


@pytest.mark.parametrize('status, expected', [(400, 'schema'), (401, 'không hợp lệ'),
                                               (402, 'prepaid credit'), (403, 'quyền'),
                                               (429, 'quota')])
def test_gemini_errors_are_readable_and_do_not_leak_key(status, expected, monkeypatch):
    monkeypatch.setattr(gemini.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(gemini.requests, 'post', lambda *args, **kwargs: Response(
        {'error': {'status': 'ERROR', 'message': 'request rejected secret-key'}}, status))
    with pytest.raises(RuntimeError) as error:
        gemini.generate('prompt', [], 'gemini-test', {}, 'secret-key', lambda: None)
    assert expected in str(error.value)
    assert 'secret-key' not in str(error.value)
