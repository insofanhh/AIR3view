import copy
import io
import json
from pathlib import Path
from typing import Literal

import pytest
from pydantic import Field, ValidationError

from backend import providers, batches
from backend.ai_schema import AISchemaError, strict_schema, response_models, preflight
from backend.ai_errors import AIProviderError, codex_failure
from backend.models import Model, Settings
from backend.reaction_dubbing import DubReview


def assert_strict(node):
    if not isinstance(node, dict):
        return
    assert 'default' not in node
    if node.get('type') == 'object':
        assert set(node['required']) == set(node['properties'])
        assert node['additionalProperties'] is False
    for value in node.values():
        if isinstance(value, dict):
            assert_strict(value)
        elif isinstance(value, list):
            for child in value:
                assert_strict(child)


@pytest.mark.parametrize('model', response_models(), ids=lambda m: m.__name__)
def test_registered_transport_contracts_are_strict_without_changing_models(model):
    original = copy.deepcopy(model.model_json_schema())
    schema = strict_schema(model)
    assert_strict(schema)
    assert model.model_json_schema() == original


def test_dub_review_requires_issue_even_for_valid_dialogue():
    item = strict_schema(DubReview)['$defs']['DubReviewItem']
    assert item['required'] == ['id', 'valid', 'issue']
    with pytest.raises(ValidationError):
        DubReview.model_validate({'items': [{'id': 'dub0', 'valid': True}]})
    assert DubReview.model_validate({'items': [{'id': 'dub0', 'valid': True, 'issue': ''}]}).items[0].valid


def test_defaults_nullable_and_literal_do_not_become_ambiguous():
    class Item(Model):
        name: str = 'unknown'
        optional: int | None = None
        kind: Literal['dialogue'] = 'dialogue'
    class Reply(Model):
        items: list[Item] = Field(default_factory=list)
    schema = strict_schema(Reply)
    assert_strict(schema)
    properties = schema['$defs']['Item']['properties']
    assert properties['name']['type'] == 'string'
    assert {'type': 'null'} in properties['optional']['anyOf']
    assert properties['kind']['enum'] == ['dialogue']


def test_free_form_maps_fail_preflight_instead_of_silently_losing_keys():
    class Unsafe(Model):
        items: dict[str, str]
    with pytest.raises(AISchemaError, match='items') as exc:
        strict_schema(Unsafe)
    assert exc.value.retryable is False
    preflight('gemini')  # provider-specific adapter is unaffected


def test_broken_ref_is_rejected():
    class Broken:
        @classmethod
        def model_json_schema(cls):
            return {'type': 'object', 'properties': {'value': {'$ref': '#/$defs/Missing'}}}
    with pytest.raises(AISchemaError, match='reference'):
        strict_schema(Broken)


def test_schema_generation_failure_is_not_a_duration_repair_error():
    class Broken(Model):
        value: str = Field(json_schema_extra={'$ref': '#/$defs/Missing'})
    with pytest.raises(AISchemaError, match='reference'):
        strict_schema(Broken)


def test_codex_schema_error_is_actionable_and_has_no_source_text():
    details = 'PRIVATE SOURCE\nwarning: Code Mode is unavailable because code-mode host is disabled.\nERROR: ' + json.dumps({
        'type': 'error', 'error': {'code': 'invalid_json_schema',
                                 'message': "Invalid schema. Missing 'issue'."}})
    error = codex_failure(details)
    assert error.code == 'invalid_json_schema' and not error.retryable
    assert 'issue' in str(error)
    assert 'PRIVATE SOURCE' not in str(error) and 'Kiểm tra kết nối' not in str(error)
    contaminated = str(error) + ' timeout rate limit 503 AI sửa thời lượng không hợp lệ'
    assert not batches.retryable_error(contaminated)
    assert not batches.pipeline_retryable_error(contaminated)


@pytest.mark.parametrize('text,code,retryable', [
    ('ERROR: unauthorized 401', 'authentication', False),
    ("ERROR: You've hit your usage limit", 'quota', False),
    ('ERROR: rate limit reached', 'rate_limit', True),
    ('ERROR: connection reset', 'transport', True),
    ('ERROR: timed out', 'timeout', True),
    ('warning: Code Mode disabled', 'unknown', False),
])
def test_codex_failure_categories(text, code, retryable):
    error = codex_failure(text)
    assert (error.code, error.retryable) == (code, retryable)


def install_fake_codex(monkeypatch, payload=None, error=None):
    monkeypatch.setattr(providers, 'codex_binary', lambda: 'test-codex')
    calls = []
    class Process:
        def __init__(self, args, **kwargs):
            calls.append(args)
            self.stdin = io.BytesIO()
            schema_path = Path(args[args.index('--output-schema') + 1])
            assert_strict(json.loads(schema_path.read_text('utf-8')))
            self.returncode = 1 if error else 0
            if error:
                kwargs['stdout'].write(('PRIVATE SOURCE\nERROR: ' + json.dumps({'error': error})).encode())
            else:
                Path(args[args.index('--output-last-message') + 1]).write_text(json.dumps(payload), 'utf-8')
        def poll(self):
            return self.returncode
    monkeypatch.setattr(providers.subprocess, 'Popen', Process)
    return calls


def test_codex_transport_strict_schema_cache_and_safe_failure_log(tmp_path, monkeypatch):
    settings = Settings(provider='codex').model_dump()
    calls = install_fake_codex(monkeypatch, error={'code': 'invalid_json_schema', 'message': "Missing 'issue'."})
    with pytest.raises(AIProviderError):
        providers.ask_ai('PRIVATE SOURCE', [], settings, tmp_path, lambda: None, DubReview)
    assert len(calls) == 1
    failure = next((tmp_path / 'analysis-cache').glob('*.failure.json'))
    content = failure.read_text('utf-8')
    record = json.loads(content)
    assert (record['response_model'], record['code'], record['retryable']) == ('DubReview', 'invalid_json_schema', False)
    assert 'PRIVATE SOURCE' not in content
    assert not list((tmp_path / 'analysis-cache').glob('*.response.json'))
    payload = {'items': [{'id': 'dub0', 'valid': True, 'issue': ''}]}
    calls = install_fake_codex(monkeypatch, payload=payload)
    assert providers.ask_ai('PRIVATE SOURCE', [], settings, tmp_path, lambda: None, DubReview) == payload
    assert providers.ask_ai('PRIVATE SOURCE', [], settings, tmp_path, lambda: None, DubReview) == payload
    assert len(calls) == 1
    call = json.loads(next((tmp_path / 'analysis-cache').glob('*.call.json')).read_text('utf-8'))
    assert call['cache_hit'] and call['request_count'] == 0


@pytest.mark.parametrize('has_issue', [False, True])
def test_legacy_dub_review_cache_only_accepts_complete_valid_contract(tmp_path, monkeypatch, has_issue):
    settings = Settings(provider='codex').model_dump()
    old_schema = copy.deepcopy(DubReview.model_json_schema())
    old_schema['$defs']['DubReviewItem']['required'] = ['id', 'valid']
    old_schema['$defs']['DubReviewItem']['properties']['issue']['default'] = ''
    old_hash = providers.digest({'prompt': 'Review', 'images': [], 'provider': 'codex', 'model': '', 'schema': old_schema})
    cache = tmp_path / 'analysis-cache'
    cache.mkdir()
    item = {'id': 'dub0', 'valid': True}
    if has_issue:
        item['issue'] = ''
    (cache / (old_hash + '.json')).write_text(json.dumps({'items': [item]}), 'utf-8')
    calls = install_fake_codex(monkeypatch, payload={'items': [{'id': 'dub0', 'valid': True, 'issue': ''}]})
    providers.ask_ai('Review', [], settings, tmp_path, lambda: None, DubReview)
    assert len(calls) == (0 if has_issue else 1)


def test_legacy_schema_cache_keeps_expensive_accepted_analysis(tmp_path, monkeypatch):
    settings = Settings(provider='codex').model_dump()
    class Reply(Model):
        text: str
        note: str = ''
    legacy_hash = providers.digest({'prompt': 'Analyze', 'images': [], 'provider': 'codex', 'model': '',
                                    'schema': Reply.model_json_schema()})
    cache = tmp_path / 'analysis-cache'
    cache.mkdir()
    (cache / (legacy_hash + '.json')).write_text('{"text":"Already analyzed"}', 'utf-8')
    monkeypatch.setattr(providers, 'codex_binary', lambda: pytest.fail('Must reuse validated legacy result'))
    assert providers.ask_ai('Analyze', [], settings, tmp_path, lambda: None, Reply) == {'text': 'Already analyzed', 'note': ''}


def test_openai_receives_strict_contract_and_schema_400_does_not_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(providers, 'key', lambda *a: 'secret-key')
    calls = []
    class Response:
        status_code, ok = 400, False
        def json(self):
            return {'error': {'code': 'invalid_json_schema', 'message': "Missing 'issue'."}}
    def post(*args, **kwargs):
        calls.append(kwargs['json'])
        assert_strict(kwargs['json']['text']['format']['schema'])
        return Response()
    monkeypatch.setattr(providers.requests, 'post', post)
    with pytest.raises(AIProviderError, match='invalid_json_schema'):
        providers.ask_ai('PRIVATE SOURCE', [], Settings(provider='openai', model='test-model').model_dump(),
                         tmp_path, lambda: None, DubReview)
    assert len(calls) == 1
    data = next((tmp_path / 'analysis-cache').glob('*.failure.json')).read_text('utf-8')
    assert 'PRIVATE SOURCE' not in data and 'secret-key' not in data


def test_preflight_failure_keeps_model_path_and_diagnostic_metadata(tmp_path, monkeypatch):
    class Unsafe(Model):
        data: dict[str, str]
    monkeypatch.setattr(providers, 'response_models', lambda: (Unsafe,))
    with pytest.raises(AISchemaError, match='Unsafe'):
        providers.preflight_ai(Settings().model_dump(), tmp_path, lambda: None)
    record = json.loads(next((tmp_path / 'analysis-cache').glob('preflight-*.failure.json')).read_text('utf-8'))
    assert record['schema_path'] == '#/properties/data'
    assert record['response_model'] == 'Unsafe' and not record['retryable']


def test_diagnostic_write_failure_never_turns_cache_hit_into_paid_request(tmp_path, monkeypatch):
    settings = Settings(provider='codex').model_dump()
    payload = {'items': [{'id': 'dub0', 'valid': True, 'issue': ''}]}
    calls = install_fake_codex(monkeypatch, payload=payload)
    providers.ask_ai('Review', [], settings, tmp_path, lambda: None, DubReview)
    replace = Path.replace
    def fail_metadata(path, destination):
        if str(destination).endswith('.call.json'):
            raise PermissionError('Locked diagnostics')
        return replace(path, destination)
    monkeypatch.setattr(Path, 'replace', fail_metadata)
    assert providers.ask_ai('Review', [], settings, tmp_path, lambda: None, DubReview) == payload
    assert len(calls) == 1
    assert not list((tmp_path / 'analysis-cache').glob('*.tmp'))
