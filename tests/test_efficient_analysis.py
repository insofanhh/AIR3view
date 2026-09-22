import copy
import json
import pytest

from backend import efficient_analysis, providers, store


def _project(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'DB', tmp_path / 'studio.sqlite3')
    store.init()
    project = store.create('Efficient', {'kind': 'upload', 'file': 'source.mp4'})
    folder = store.project_dir(project['id'])
    frames = []
    for index, moment in enumerate((0, 10, 20, 30, 40, 49.9)):
        relative = f'frames/{index}.jpg'
        path = folder / relative
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(f'frame-{index}'.encode())
        frames.append({'time': moment, 'file': relative})
    project.update(metadata={'duration': 50, 'has_audio': False}, frames=frames,
                   transcript=[{'id': 'c0', 'start': 1, 'end': 2, 'text': 'The suspect arrives.'},
                               {'id': 'c1', 'start': 44, 'end': 48, 'text': 'The officer explains the outcome.'}],
                   source_transcript=[])
    project['settings'].update(output_mode='single', summary_seconds=30, review_enabled=False)
    return project


def test_efficient_pipeline_uses_bounded_transcript_and_vision_calls(monkeypatch, tmp_path):
    project = _project(tmp_path, monkeypatch)
    calls = []

    def fake_ask(prompt, images, settings, folder, check, response_model):
        calls.append((prompt, list(images), response_model.__name__))
        if response_model is efficient_analysis.EvidenceAnswer:
            later = bool(images and images[0].name in {'3.jpg', '4.jpg', '5.jpg'})
            return {'scenes': [{'start': 1 if not images else (31 if later else 1), 'end': 2 if not images else (32 if later else 2), 'description': 'The encounter develops.',
                                'characters': ['officer'], 'evidence': 'Dialogue and supplied frame.',
                                'confidence': .9}], 'summary': 'The encounter develops and ends.', 'uncertainties': []}
        raise AssertionError('Final planning is patched below')

    monkeypatch.setattr(providers, 'ask_ai', fake_ask)
    monkeypatch.setattr(efficient_analysis, '_select_frames', lambda *args: project['frames'])
    monkeypatch.setattr(efficient_analysis, '_image_batches', lambda frames: [frames[:3], frames[3:]])
    monkeypatch.setattr('backend.story.plan_story', lambda p, report, check: {**p, 'story_plan': {'ok': True}})
    result = efficient_analysis.analyze_efficient(project, lambda *args: None, lambda: None)
    assert len(calls) == 3
    assert sum(bool(images) for _, images, _ in calls) == 2
    assert result['evidence_manifest']['stats']['calls'] == 3
    assert result['story_plan']['ok'] is True
    assert store.read(project['id'])['scenes'][0]['description'] == 'The encounter develops.'


def test_efficient_evidence_cache_reuses_source_stages(monkeypatch, tmp_path):
    project = _project(tmp_path, monkeypatch)
    monkeypatch.setattr(efficient_analysis, '_select_frames', lambda *args: project['frames'])
    monkeypatch.setattr(efficient_analysis, '_image_batches', lambda frames: [frames])
    calls = []

    def fake_ask(*args, **kwargs):
        calls.append(True)
        return {'scenes': [{'start': 1 if not args[1] else 0, 'end': 48 if not args[1] else 49, 'description': 'Complete source.',
                            'characters': [], 'evidence': 'Transcript and frames.', 'confidence': .95}],
                'summary': 'Complete source.', 'uncertainties': []}

    monkeypatch.setattr(providers, 'ask_ai', fake_ask)
    monkeypatch.setattr('backend.story.plan_story', lambda p, report, check: p)
    first = efficient_analysis.analyze_efficient(project, lambda *args: None, lambda: None)
    second = efficient_analysis.analyze_efficient(store.read(project['id']), lambda *args: None, lambda: None)
    assert len(calls) == 2
    assert first['evidence_manifest']['stats']['calls'] == 2
    assert second['evidence_manifest']['stats']['cache_hits'] == 2


def evidence(start, end, point=False, uncertainties=None):
    return {'scenes': [{'start': start, 'end': end, 'point': point,
                       'description': 'Visible state', 'characters': [],
                       'evidence': 'Supplied source evidence', 'confidence': .9}],
            'summary': 'Observed state', 'uncertainties': uncertainties or []}


def test_exact_failed_frame_is_a_point_not_fabricated_interval():
    raw = evidence(748.067, 748.067, uncertainties=[
        {'start': 0, 'end': 127.467, 'reason': 'Earlier context'},
        {'start': 411.067, 'end': 748.067, 'reason': 'Sparse observation'}])
    result = efficient_analysis._validate_answer(raw, 127.467, 750.566, 750.566, [411.067, 748.067])
    assert result['scenes'][0]['point'] is True
    assert result['scenes'][0]['start'] == result['scenes'][0]['end'] == 748.067
    assert result['uncertainties'] == raw['uncertainties'][1:]


@pytest.mark.parametrize('raw,times', [
    (evidence(748, 748, True), [748.067]),
    (evidence(748.067, 748.067, True), []),
    (evidence(150, 149), [150]),
    (evidence(float('nan'), 160), [150]),
    (evidence(150, float('inf')), [150]),
    (evidence(120, 150), [150]),
    (evidence(150, 151, True), [150]),
])
def test_invalid_or_unproven_points_and_intervals_rejected(raw, times):
    with pytest.raises(ValueError):
        efficient_analysis._validate_answer(raw, 127.467, 750.566, 750.566, times)


def test_tiny_boundary_clamp_cannot_create_a_point():
    result = efficient_analysis._validate_answer(evidence(127.44, 750.59), 127.467, 750.566, 750.566)
    assert (result['scenes'][0]['start'], result['scenes'][0]['end']) == (127.467, 750.566)
    with pytest.raises(ValueError, match='khoảng rỗng'):
        efficient_analysis._validate_answer(evidence(127.44, 127.467), 127.467, 750.566, 750.566, [127.467])


def test_uncertainty_outside_source_is_not_silently_clamped():
    raw = evidence(150, 151, uncertainties=[{'start': -100, 'end': 800, 'reason': 'Bad'}])
    with pytest.raises(ValueError, match='vượt nguồn'):
        efficient_analysis._validate_answer(raw, 127, 751, 751)


def test_dense_early_shots_do_not_starve_final_half(tmp_path):
    frames = []
    for i in range(151):
        path = tmp_path / f'{i}.jpg'
        path.write_bytes(b'frame')
        frames.append({'time': i * 5, 'file': path.name})
    project = {'metadata': {'duration': 750.566}, 'frames': frames,
               'shots': [{'start': i, 'end': i + 1} for i in range(100)]}
    chosen = efficient_analysis._select_frames(project, tmp_path, [], [{'start': 0, 'end': 750}])
    times = [f['time'] for f in chosen]
    assert times[0] == 0 and times[-1] == 750
    assert len(times) <= 36
    assert max(b - a for a, b in zip(times, times[1:])) <= 50
    ranges = efficient_analysis._batch_ranges(efficient_analysis._image_batches(chosen), 750.566)
    assert ranges[0][0] == 0 and ranges[-1][1] == 750.566
    assert all(a[1] == b[0] for a, b in zip(ranges, ranges[1:]))


def test_cache_key_changes_with_batch_bounds_and_prompt():
    frames = [{'time': 748.067, 'sha256': 'content', 'file': 'last.jpg'}]
    args = ({}, 'transcript', [], frames, 'gemini', 'model')
    original = efficient_analysis._vision_cache_key(*args, 127, 750.566, 'prompt')
    assert original != efficient_analysis._vision_cache_key(*args, 128, 750.566, 'prompt')
    assert original != efficient_analysis._vision_cache_key(*args, 127, 750.566, 'new prompt')


def test_repair_is_bounded_and_quarantines_provider_failure(tmp_path, monkeypatch):
    settings = {'provider': 'gemini', 'model': 'test'}
    calls = []
    folder = tmp_path / 'analysis-cache'
    folder.mkdir()
    stage = tmp_path / 'stages'
    stage.mkdir()
    def bad_response(prompt, images, settings, folder, check, response_model):
        calls.append(prompt)
        fingerprint = providers.digest({'prompt': prompt, 'images': [], 'provider': 'gemini', 'model': 'test',
                                        'schema': response_model.model_json_schema()})
        raw = evidence(0, 20)
        (folder / 'analysis-cache' / (fingerprint + '.json')).write_text(json.dumps(raw))
        return raw
    monkeypatch.setattr(providers, 'ask_ai', bad_response)
    with pytest.raises(ValueError, match='127'):
        efficient_analysis._call_evidence('Prompt', [], settings, tmp_path, lambda: None,
                                         stage, 'key', 127, 751, 751,
                                         {'calls': 0, 'cache_hits': 0, 'images': 0})
    assert len(calls) == 2 and 'scenes[0].start=0' in calls[1]
    assert not (stage / 'key.json').exists()
    assert len(list(folder.glob('*.invalid.json'))) == 2


def test_wire_schema_requires_all_scene_fields():
    schema = efficient_analysis.EvidenceAnswer.model_json_schema()['$defs']['EvidenceScene']
    assert set(schema['required']) == set(schema['properties'])
