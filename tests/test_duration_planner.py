import copy
import pytest

from backend import story, providers
from backend.models import Settings


def project():
    return {'id': 'e' * 32, 'metadata': {'duration': 750.566, 'has_audio': True},
            'settings': Settings(output_mode='single', summary_seconds=540,
                                 narration_style='storytelling', original_dialogue_ratio=.2).model_dump(),
            'transcript': [], 'scenes': [], 'summary': 'Source ends peacefully.',
            'hooks': [], 'narrations': [], 'source': {'file': 'source.mp4'}}


def short_plan():
    return {'title': 'Title', 'synopsis': 'Events', 'outcome': 'Resolution', 'lesson': 'Lesson',
            'hook': {'start': 396, 'end': 400.36, 'title': 'Hook', 'reason': 'Evidence'},
            'selections': [{'start': a, 'end': b, 'part': 1, 'section': section,
                            'reason': 'Source', 'priority': .9, 'narration': 'Words',
                            'narration_offset': 0, 'evidence': 'Evidence'}
                           for a, b, section in [(0, 23.24, 'opening'), (100, 125, 'development'),
                                                 (700, 717.1, 'ending')]]}


def test_real_short_draft_rejected_with_measured_deficit():
    with pytest.raises(ValueError, match=r'thiếu 335\.3s'):
        story.validate_plan(short_plan(), project())


def test_prompt_has_hard_duration_and_narration_budgets():
    text = story.duration_planning_instructions(project(), 3.645)
    assert 'minimum 405.0s' in text and 'maximum 540.0s' in text
    assert 'words/syllables per part' in text and 'narrated clips per part' in text
    assert 'NOT mandatory clip boundaries' in text


def test_duration_retry_rebuilds_instead_of_copying_short_draft(monkeypatch):
    p = project()
    calls = []
    monkeypatch.setattr(story, 'speech_rate', lambda p: 3.645)
    def fake(prompt, *args):
        calls.append(prompt)
        return copy.deepcopy(short_plan())
    monkeypatch.setattr(providers, 'ask_ai', fake)
    with pytest.raises(ValueError, match='DURATION_BUDGET_FAILURE'):
        story.plan_story(p, lambda *args: None, lambda: None)
    assert len(calls) == 3
    assert 'REBUILD THE EDIT PLAN FROM SOURCE' in calls[1]
    assert '"missing_to_minimum":335.3' in calls[1]
    assert '\nDRAFT:' not in calls[1]
    assert p.get('story_plan') is None


def test_multipart_durations_count_hook_only_once():
    p = project()
    p['settings'].update(output_mode='parts', part_count=2, part_seconds=300)
    raw = short_plan()
    raw['selections'][-1]['part'] = 2
    stats = story.duration_budget_stats(raw, p)
    assert stats['totals'][1] == pytest.approx(4.36 + 23.24 + 25)
    assert stats['totals'][2] == pytest.approx(17.1)
    assert stats['minimum'] == 225


def test_target_longer_than_source_has_feasible_lower_bound():
    p = project()
    p['settings']['summary_seconds'] = 1500
    stats = story.duration_budget_stats({}, p)
    assert stats['minimum'] == pytest.approx(750.566 * .75)
    assert stats['target'] == 1500
