import copy
import json

import pytest

from backend import providers, store, story
from backend.media import Cancelled
from backend.models import Settings, StoryAnswer
from backend.story import plan_story
from backend.story_schedule import ScheduledNarration, write_scheduled
from backend.timeline import build


def project():
    return {
        'id': 'f' * 32,
        'source': {'kind': 'upload', 'file': 'source.mp4'},
        'metadata': {'duration': 60, 'has_audio': False},
        'settings': Settings(
            output_mode='single', summary_seconds=40, language='English',
            narration_style='storytelling', voice_speed=1,
        ).model_dump(),
        'narrations': [], 'transcript': [], 'scenes': [],
        'summary': 'The disagreement is resolved without further incident.',
        'hooks': [], 'warnings': [], 'revision': 0,
    }


def short_story_answer():
    return {
        'title': 'A complete story',
        'synopsis': 'An argument is resolved.',
        'outcome': 'The people reach an agreement.',
        'lesson': 'Calm communication helps.',
        'hook': {'start': 22, 'end': 25, 'title': 'An argument', 'reason': 'Raised voices'},
        'selections': [
            {'start': 0, 'end': 10, 'part': 1, 'section': 'opening',
             'reason': 'Context', 'priority': .9, 'narration': 'short',
             'narration_offset': 0, 'evidence': 'The disagreement begins.'},
            {'start': 20, 'end': 30, 'part': 1, 'section': 'development',
             'reason': 'Exchange', 'priority': .9, 'narration': 'short',
             'narration_offset': 0, 'evidence': 'Both sides explain their position.'},
            {'start': 50, 'end': 60, 'part': 1, 'section': 'ending',
             'reason': 'Resolution', 'priority': .9, 'narration': 'short',
             'narration_offset': 0, 'evidence': 'The people reach an agreement.'},
        ],
    }


def requested_slots(prompt):
    payload = prompt.split('\nREQUESTED SLOTS: ', 1)[1].split('\nACCEPTED IMMUTABLE LINES: ', 1)[0]
    return json.loads(payload)


def line(word, count=22):
    return ' '.join([word] * count)


def test_plan_story_repairs_only_failed_slots_and_builds_timeline(tmp_path, monkeypatch):
    """A valid line must survive while a short sibling is repaired.

    This exercises the same final fallback as a full Analyze/Run-all job: the
    first three StoryAnswer drafts have valid footage but under-filled prose,
    then the scheduler writes duration-bounded lines and hands the accepted
    plan to the normal timeline builder.
    """
    monkeypatch.setattr(store, 'project_dir', lambda _pid: tmp_path)
    schedule_prompts = []

    def fake(prompt, _images, _settings, _folder, _check, response_model=None):
        if response_model is StoryAnswer:
            return copy.deepcopy(short_story_answer())
        assert response_model is ScheduledNarration
        schedule_prompts.append(prompt)
        ids = [slot['id'] for slot in requested_slots(prompt)]
        if len(schedule_prompts) == 1:
            assert ids == ['0', '1', '2']
            return {'items': [
                {'id': '0', 'text': line('opening')},
                {'id': '1', 'text': 'too short'},
                {'id': '2', 'text': line('ending')},
            ]}
        assert ids == ['1']
        return {'items': [{'id': '1', 'text': line('development')}]}

    monkeypatch.setattr(providers, 'ask_ai', fake)
    result = plan_story(project(), lambda *_args: None, lambda: None)

    assert len(schedule_prompts) == 2
    assert line('opening') in schedule_prompts[1]
    assert line('ending') in schedule_prompts[1]
    assert [n['text'] for n in result['narrations']] == [
        line('opening'), line('development'), line('ending'),
    ]
    timeline = build(result)
    assert timeline['planned'] is True
    assert timeline['duration'] == pytest.approx(33)
    assert [clip['kind'] for clip in timeline['clips']] == [
        'hook', 'highlight', 'highlight', 'highlight',
    ]


def test_failed_run_advances_repair_generation_for_next_retry(tmp_path, monkeypatch):
    """A user Retry must not replay the provider's poisoned prompt cache."""
    monkeypatch.setattr(store, 'project_dir', lambda _pid: tmp_path)
    schedule_prompts = []
    repair = {'enabled': False}

    def fake(prompt, _images, _settings, _folder, _check, response_model=None):
        if response_model is StoryAnswer:
            return copy.deepcopy(short_story_answer())
        assert response_model is ScheduledNarration
        schedule_prompts.append(prompt)
        slots = requested_slots(prompt)
        count = 22 if repair['enabled'] else 2
        return {'items': [{'id': slot['id'], 'text': line('word', count)} for slot in slots]}

    monkeypatch.setattr(providers, 'ask_ai', fake)
    first = plan_story(project(), lambda *_args: None, lambda: None)
    assert len(schedule_prompts) == 6
    assert len(first['narrations']) == 3

    repair['enabled'] = True
    result = plan_story(project(), lambda *_args: None, lambda: None)
    assert 'REPAIR GENERATION: 7' in schedule_prompts[6]
    assert len(result['narrations']) == 3


def test_missing_and_duplicate_ids_are_repaired_without_rewriting_valid_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(story, 'speech_rate', lambda _project: 2.7)
    prompts = []

    def fake(prompt, *_args):
        prompts.append(prompt)
        ids = [slot['id'] for slot in requested_slots(prompt)]
        if len(prompts) == 1:
            assert ids == ['0', '1', '2']
            return {'items': [
                {'id': '0', 'text': line('opening')},
                {'id': '1', 'text': line('duplicate')},
                {'id': '1', 'text': line('duplicate')},
            ]}
        assert ids == ['1', '2']
        return {'items': [
            {'id': '1', 'text': line('development')},
            {'id': '2', 'text': line('ending')},
        ]}

    result = write_scheduled(short_story_answer(), project(), fake, tmp_path,
                             lambda *_args: None, lambda: None)
    assert len(prompts) == 2
    assert line('opening') in prompts[1]
    assert [item['narration'] for item in result['selections']] == [
        line('opening'), line('development'), line('ending'),
    ]


@pytest.mark.parametrize('error', [RuntimeError('provider unavailable'), Cancelled('cancelled')])
def test_provider_and_cancellation_errors_propagate_immediately(tmp_path, monkeypatch, error):
    monkeypatch.setattr(story, 'speech_rate', lambda _project: 2.7)
    calls = []

    def fail(*_args):
        calls.append(True)
        raise error

    with pytest.raises(type(error), match=str(error)):
        write_scheduled(short_story_answer(), project(), fail, tmp_path,
                        lambda *_args: None, lambda: None)
    assert len(calls) == 1


def test_successful_word_repairs_still_run_final_plan_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(story, 'speech_rate', lambda _project: 2.7)

    def valid(prompt, *_args):
        return {'items': [
            {'id': slot['id'], 'text': line('word')}
            for slot in requested_slots(prompt)
        ]}

    def reject(*_args, **_kwargs):
        raise ValueError('final validation called')

    monkeypatch.setattr(story, 'validate_plan', reject)
    with pytest.raises(ValueError, match='final validation called'):
        write_scheduled(short_story_answer(), project(), valid, tmp_path,
                        lambda *_args: None, lambda: None)


@pytest.mark.parametrize('bad_text',['', 'Các cảnh sát đã được đưa đến một khu vực trong rừng để tìm những người mất tích.'])
def test_deferral_to_audio_does_not_accept_empty_or_wrong_language(tmp_path,bad_text):
    def bad(prompt,*args):
        return {'items':[{'id':s['id'],'text':bad_text} for s in requested_slots(prompt)]}
    with pytest.raises(ValueError,match='6 lượt sửa'):
        write_scheduled(short_story_answer(),project(),bad,tmp_path,lambda *a:None,lambda:None)
