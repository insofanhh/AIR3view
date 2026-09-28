import json

import pytest

from backend import efficient_analysis, providers, store
from backend.plan_first import _openai_plan_context
from backend.models import Settings
from backend.voice_repair import NarrationReply


class Response:
    def __init__(self, status, payload, headers=None):
        self.status_code = status
        self.payload = payload
        self.headers = headers or {}
        self.ok = status < 400
        self.text = json.dumps(payload)

    def json(self):
        return self.payload


def _settings():
    return Settings(provider='openai', model='gpt-5.6-sol').model_dump()


def test_oversized_tpm_request_fails_once_without_waiting(tmp_path, monkeypatch):
    monkeypatch.setattr(providers, 'key', lambda *args: 'test-key')
    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs['json'])
        return Response(429, {'error': {'message': 'tokens per min (TPM): Limit 10000, Requested 11977.',
                                         'code': 'rate_limit_exceeded'}})

    monkeypatch.setattr(providers.requests, 'post', post)
    with pytest.raises(providers.OpenAIRequestTooLarge) as error:
        providers.ask_ai('A large prompt', [], _settings(), tmp_path, lambda: None, NarrationReply)
    assert (error.value.limit, error.value.requested) == (10000, 11977)
    assert len(calls) == 1
    assert calls[0]['max_output_tokens'] == 2000


def test_temporary_429_retries_then_caches_success(tmp_path, monkeypatch):
    monkeypatch.setattr(providers, 'key', lambda *args: 'test-key')
    replies = iter([Response(429, {'error': {'message': 'Rate limit reached', 'code': 'rate_limit_exceeded'}},
                             {'Retry-After': '0'}),
                    Response(200, {'status': 'completed', 'output': [
                        {'content': [{'type': 'output_text', 'text': '{"text":"Hello."}'}]}], 'usage': {}})])
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        return next(replies)

    monkeypatch.setattr(providers.requests, 'post', post)
    assert providers.ask_ai('A short prompt', [], _settings(), tmp_path, lambda: None, NarrationReply) == {'text': 'Hello.'}
    assert providers.ask_ai('A short prompt', [], _settings(), tmp_path, lambda: None, NarrationReply) == {'text': 'Hello.'}
    assert len(calls) == 2


def test_quota_429_does_not_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(providers, 'key', lambda *args: 'test-key')
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        return Response(429, {'error': {'message': 'Insufficient quota', 'code': 'insufficient_quota'}})

    monkeypatch.setattr(providers.requests, 'post', post)
    with pytest.raises(RuntimeError, match='quota'):
        providers.ask_ai('A short prompt', [], _settings(), tmp_path, lambda: None, NarrationReply)
    assert len(calls) == 1


def test_token_reset_header_controls_transient_backoff():
    response = Response(429, {'error': {'code': 'rate_limit_exceeded'}},
                        {'x-ratelimit-reset-tokens': '1m2.5s'})
    assert providers._openai_retry_delay(response, 0) == 60
    response.headers['x-ratelimit-reset-tokens'] = '750ms'
    assert providers._openai_retry_delay(response, 0) == .75


def test_incomplete_response_logs_reason_and_usage_without_source(tmp_path, monkeypatch):
    monkeypatch.setattr(providers, 'key', lambda *args: 'test-key')
    monkeypatch.setattr(providers.requests, 'post', lambda *args, **kwargs: Response(200, {
        'id': 'resp-test', 'status': 'incomplete',
        'incomplete_details': {'reason': 'max_output_tokens'},
        'usage': {'input_tokens': 5000, 'output_tokens': 2500,
                  'output_tokens_details': {'reasoning_tokens': 400}},
        'output': []}))
    with pytest.raises(providers.OpenAIOutputIncomplete) as error:
        providers.ask_ai('PRIVATE SOURCE DIALOGUE', [], _settings(), tmp_path, lambda: None, NarrationReply)
    assert error.value.reason == 'max_output_tokens'
    files = list((tmp_path / 'analysis-cache').glob('*.failure.json'))
    assert len(files) == 1
    raw = files[0].read_text('utf-8')
    record = json.loads(raw)
    assert (record['input_tokens'], record['output_tokens'], record['reasoning_tokens']) == (5000, 2500, 400)
    assert record['reason'] == 'max_output_tokens'
    assert 'PRIVATE SOURCE DIALOGUE' not in raw and 'test-key' not in raw


def test_evidence_has_room_for_structured_output_and_reasoning():
    assert providers._openai_output_cap(efficient_analysis.EvidenceAnswer) == 3500


def test_sol_footage_planning_uses_low_reasoning_and_larger_output(tmp_path, monkeypatch):
    from backend.plan_first import FootagePlan
    monkeypatch.setattr(providers, 'key', lambda *args: 'test-key')
    captured = []

    def post(*args, **kwargs):
        captured.append(json.loads(json.dumps(kwargs['json'])))
        return Response(200, {'status': 'incomplete',
                              'incomplete_details': {'reason': 'max_output_tokens'},
                              'usage': {'input_tokens': 4000, 'output_tokens': 5200}, 'output': []})

    monkeypatch.setattr(providers.requests, 'post', post)
    with pytest.raises(providers.OpenAIOutputIncomplete):
        providers.ask_ai('Planner', [], _settings(), tmp_path, lambda: None, FootagePlan)
    assert captured[0]['max_output_tokens'] == 5200
    assert captured[0]['reasoning'] == {'effort': 'low'}


def test_sol_coverage_review_reserves_output_with_low_reasoning(tmp_path, monkeypatch):
    from backend.story_bridges import CoverageReview
    monkeypatch.setattr(providers, 'key', lambda *args: 'test-key')
    captured = []

    def post(*args, **kwargs):
        captured.append(kwargs['json'])
        return Response(200, {'status': 'completed', 'output': [
            {'content': [{'type': 'output_text', 'text': '{"complete":true,"issues":[],"repairs":[]}'}]}],
            'usage': {}})

    monkeypatch.setattr(providers.requests, 'post', post)
    assert providers.ask_ai('Review', [], _settings(), tmp_path, lambda: None, CoverageReview)['complete']
    assert captured[0]['max_output_tokens'] == 3500
    assert captured[0]['reasoning'] == {'effort': 'low'}


def test_sol_narration_repair_has_room_for_reasoning_and_json(tmp_path, monkeypatch):
    monkeypatch.setattr(providers, 'key', lambda *args: 'test-key')
    captured = []

    def post(*args, **kwargs):
        captured.append(kwargs['json'])
        return Response(200, {'status': 'completed', 'output': [
            {'content': [{'type': 'output_text', 'text': '{"text":"Short corrected narration."}'}]}],
            'usage': {}})

    monkeypatch.setattr(providers.requests, 'post', post)
    assert providers.ask_ai('Repair', [], _settings(), tmp_path, lambda: None, NarrationReply)['text']
    assert captured[0]['max_output_tokens'] == 2000
    assert captured[0]['reasoning'] == {'effort': 'low'}


def test_sol_retries_all_reasoning_response_without_reasoning(tmp_path, monkeypatch):
    monkeypatch.setattr(providers, 'key', lambda *args: 'test-key')
    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs['json']['reasoning']['effort'])
        if len(calls) == 1:
            return Response(200, {'status': 'incomplete',
                                  'incomplete_details': {'reason': 'max_output_tokens'},
                                  'usage': {'output_tokens': 2000,
                                            'output_tokens_details': {'reasoning_tokens': 2000}},
                                  'output': []})
        return Response(200, {'status': 'completed', 'usage': {}, 'output': [
            {'content': [{'type': 'output_text', 'text': '{"text":"Recovered."}'}]}]})

    monkeypatch.setattr(providers.requests, 'post', post)
    assert providers.ask_ai('Repair', [], _settings(), tmp_path, lambda: None, NarrationReply)['text'] == 'Recovered.'
    assert calls == ['low', 'none']
    assert not list((tmp_path / 'analysis-cache').glob('*.failure.json'))


def test_sol_srt_cleanup_disables_hidden_reasoning(tmp_path, monkeypatch):
    from backend.reaction_cops import OptimizedBatch
    monkeypatch.setattr(providers, 'key', lambda *args: 'test-key')
    captured = []

    def post(*args, **kwargs):
        captured.append(kwargs['json'])
        return Response(200, {'status': 'completed', 'usage': {}, 'output': [
            {'content': [{'type': 'output_text', 'text': '{"cues":[{"source_cue_ids":["a"],"text":"Hello."}],"warnings":[]}'}]}]})

    monkeypatch.setattr(providers.requests, 'post', post)
    providers.ask_ai('Cleanup', [], _settings(), tmp_path, lambda: None, OptimizedBatch)
    assert captured[0]['reasoning'] == {'effort': 'none'}


def test_reaction_plan_recovers_when_partial_json_exhausts_output(tmp_path, monkeypatch):
    from backend.reaction_cops import ReactionFootagePlan
    monkeypatch.setattr(providers, 'key', lambda *args: 'test-key')
    efforts = []

    def post(*args, **kwargs):
        body = kwargs['json']
        efforts.append((body['reasoning']['effort'], body['max_output_tokens']))
        if len(efforts) == 1:
            return Response(200, {'status': 'incomplete',
                                  'incomplete_details': {'reason': 'max_output_tokens'},
                                  'usage': {'output_tokens': 5200,
                                            'output_tokens_details': {'reasoning_tokens': 2347}},
                                  'output': [{'content': [{'type': 'output_text', 'text': '{"title":"partial"'}]}]})
        return Response(200, {'status': 'completed', 'usage': {}, 'output': [
            {'content': [{'type': 'output_text', 'text': '{"title":"Plan","synopsis":"Scene","last_confirmed_event":"Exit","selections":[{"start":1,"end":2,"part":1,"section":"development","reason":"Exchange","priority":0.8,"source_cue_ids":["c1"],"keep_original":true}]}'}]}]})

    monkeypatch.setattr(providers.requests, 'post', post)
    result = providers.ask_ai('Plan', [], _settings(), tmp_path, lambda: None, ReactionFootagePlan)
    assert result['title'] == 'Plan'
    assert efforts == [('low', 5200), ('none', 5200)]


def test_transcript_chunking_preserves_all_cues_and_respects_limit():
    cues = [{'id': f'c{i}', 'start': i, 'end': i + .5, 'text': 'Dialogue ' * 40} for i in range(480)]
    chunks = efficient_analysis._transcript_chunks(cues)
    assert [cue['id'] for chunk in chunks for cue in chunk] == [cue['id'] for cue in cues]
    assert all(sum(len(json.dumps(c, ensure_ascii=False, separators=(',', ':'))) + 1 for c in chunk) + 2
               <= efficient_analysis._TRANSCRIPT_LIMIT for chunk in chunks)


def test_analysis_splits_only_oversized_transcript_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'DB', tmp_path / 'studio.sqlite3')
    store.init()
    project = store.create('Rate limited', {'kind': 'upload', 'file': 'source.mp4'})
    folder = store.project_dir(project['id'])
    frame = folder / 'frame.jpg'
    frame.write_bytes(b'frame')
    project.update(metadata={'duration': 10, 'has_audio': False},
                   frames=[{'time': 5, 'file': frame.name}],
                   source_transcript=[{'id': f'c{i}', 'start': i + 1, 'end': i + 1.5,
                                       'text': f'Original line {i}'} for i in range(2)])
    project['settings'].update(output_mode='', review_enabled=False, provider='openai')
    requested = []

    def fake_ask(prompt, images, settings, folder, check, response_model):
        if images:
            return {'scenes': [{'start': 5, 'end': 5, 'point': True, 'description': 'Frame',
                                'characters': [], 'evidence': 'Frame', 'confidence': .9}],
                    'summary': 'Frame', 'uncertainties': []}
        cues = json.loads(prompt.split('DỮ LIỆU: ', 1)[1].split('\nNội bộ:', 1)[0])
        requested.append([c['id'] for c in cues])
        if len(cues) > 1:
            raise providers.OpenAIRequestTooLarge(10000, 11977)
        cue = cues[0]
        return {'scenes': [{'start': cue['start'], 'end': cue['end'], 'point': False,
                            'description': cue['text'], 'characters': [],
                            'evidence': cue['text'], 'confidence': .9}],
                'summary': cue['text'], 'uncertainties': []}

    monkeypatch.setattr(providers, 'ask_ai', fake_ask)
    result = efficient_analysis.analyze_efficient(project, lambda *args: None, lambda: None)
    assert requested == [['c0', 'c1'], ['c0'], ['c1']]
    assert [s['description'] for s in result['scenes'] if s['description'].startswith('Original')] == [
        'Original line 0', 'Original line 1']


def test_vision_batch_splits_when_openai_reports_oversize(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'DB', tmp_path / 'studio.sqlite3')
    store.init()
    project = store.create('Visual rate limit', {'kind': 'upload', 'file': 'source.mp4'})
    folder = store.project_dir(project['id'])
    frames = []
    for moment in (2, 8):
        path = folder / f'{moment}.jpg'
        path.write_bytes(b'frame')
        frames.append({'time': moment, 'file': path.name})
    project.update(metadata={'duration': 10, 'has_audio': False}, frames=frames)
    project['settings'].update(output_mode='', review_enabled=False, provider='openai')
    monkeypatch.setattr(efficient_analysis, '_select_frames', lambda *args: frames)
    seen = []

    def fake_ask(prompt, images, settings, folder, check, response_model):
        seen.append(len(images))
        if len(images) > 1:
            raise providers.OpenAIRequestTooLarge(10000, 11977)
        moment = float(images[0].stem)
        return {'scenes': [{'start': moment, 'end': moment, 'point': True,
                            'description': 'Visible frame', 'characters': [],
                            'evidence': 'Supplied frame', 'confidence': .9}],
                'summary': 'Visible', 'uncertainties': []}

    monkeypatch.setattr(providers, 'ask_ai', fake_ask)
    result = efficient_analysis.analyze_efficient(project, lambda *args: None, lambda: None)
    assert seen == [2, 1, 1]
    assert [s['start'] for s in result['scenes']] == [2, 8]


def test_vision_batch_splits_when_output_budget_is_exhausted(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'DB', tmp_path / 'studio.sqlite3')
    store.init()
    project = store.create('Incomplete vision', {'kind': 'upload', 'file': 'source.mp4'})
    folder = store.project_dir(project['id'])
    frames = []
    for moment in (2, 8):
        path = folder / f'{moment}.jpg'
        path.write_bytes(b'frame')
        frames.append({'time': moment, 'file': path.name})
    project.update(metadata={'duration': 10, 'has_audio': False}, frames=frames)
    project['settings'].update(output_mode='', review_enabled=False, provider='openai')
    monkeypatch.setattr(efficient_analysis, '_select_frames', lambda *args: frames)
    seen = []

    def fake_ask(prompt, images, settings, folder, check, response_model):
        seen.append(len(images))
        if len(images) > 1:
            raise providers.OpenAIOutputIncomplete('max_output_tokens',
                                                    {'input_tokens': 5000, 'output_tokens': 2500})
        moment = float(images[0].stem)
        return {'scenes': [{'start': moment, 'end': moment, 'point': True,
                            'description': 'Visible frame', 'characters': [],
                            'evidence': 'Supplied frame', 'confidence': .9}],
                'summary': 'Visible', 'uncertainties': []}

    monkeypatch.setattr(providers, 'ask_ai', fake_ask)
    result = efficient_analysis.analyze_efficient(project, lambda *args: None, lambda: None)
    assert seen == [2, 1, 1]
    assert [s['start'] for s in result['scenes']] == [2, 8]


def test_openai_planner_context_spans_source_with_bounded_size():
    project = {'settings': {'original_dialogue_ratio': .7},
               'summary': 'A' * 12000,
               'scenes': [{'start': i, 'end': i + 1, 'description': 'D' * 500,
                           'evidence': 'E' * 500, 'confidence': .9} for i in range(500)],
               'source_transcript': [{'start': i, 'end': i + 1, 'text': 'T' * 300} for i in range(480)],
               'source_speech': {'items': [{'cue': i, 'start': i, 'end': i + 1,
                                            'text': 'R' * 300, 'role': 'participant',
                                            'confidence': .9, 'priority': .8} for i in range(480)]}}
    context = _openai_plan_context(project)
    assert (context['scenes'][0]['start'], context['scenes'][-1]['start']) == (0, 499)
    assert (context['real_speech'][0]['cue'], context['real_speech'][-1]['cue']) == (0, 479)
    assert len(json.dumps(context, ensure_ascii=False)) < 18000
