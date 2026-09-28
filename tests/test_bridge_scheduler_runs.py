import pytest

from backend.bridge_scheduler import choose_windows
from backend.models import Settings
from backend.plan_first import lock_schedule
from backend.source_policy import VERSION as SPEECH_VERSION
from backend.story_bridges import VERSION as BRIDGE_VERSION
from backend.retention import VERSION as RETENTION_VERSION


def _sections(count):
    return [{'start': index * 8, 'end': (index + 1) * 8} for index in range(count)]


def test_unbroken_ai_run_across_four_selections_is_rejected():
    with pytest.raises(ValueError, match='STORY_STRUCTURE'):
        choose_windows(_sections(4), [], maximum=0)


def test_three_adjacent_eight_second_bridges_are_within_limit():
    assert choose_windows(_sections(3), [], maximum=0) == []


def test_original_exchange_breaks_consecutive_ai_run():
    # A genuine exchange in the third selection resets the AI-run counter.
    candidate = (1.0, 16 * 30, 24 * 30, 'Real dialogue')
    assert choose_windows(_sections(4), [candidate], maximum=8 * 30) == [
        (16 * 30, 24 * 30, 'Real dialogue')]


def test_verified_source_led_plan_keeps_exact_original_exchange():
    settings = Settings(output_mode='single', summary_seconds=60,
                        original_dialogue_ratio=.7, narration_style='storytelling').model_dump()
    project = {'settings': settings, 'metadata': {'duration': 100, 'has_audio': True},
               'story_bridge_version': BRIDGE_VERSION,
               'retention_policy_version': RETENTION_VERSION,
               'source_transcript': [{'start': 6, 'end': 46, 'text': 'Complete real exchange'}],
               'source_speech': {'version': SPEECH_VERSION, 'items': [
                   {'cue': 0, 'start': 6, 'end': 46, 'text': 'Complete real exchange',
                    'role': 'participant', 'confidence': .95, 'priority': .9}]}}
    raw = {'title': 'Event', 'synopsis': 'Event', 'outcome': 'Known', 'lesson': 'Context',
           'hook': {'start': 80, 'end': 84, 'title': 'Hook', 'reason': 'Event',
                    'original_audio': False, 'narration': 'Situation.'},
           'selections': [
               {'start': a, 'end': b, 'part': 1, 'section': section,
                'reason': 'Evidence', 'priority': .9, 'evidence': 'Verified source',
                'narration': narration, 'narration_offset': 0}
               for a, b, section, narration in [
                   (0, 6, 'opening', 'Plan pending.'),
                   (6, 46, 'development', ''),
                   (46, 50, 'development', 'Plan pending.'),
                   (70, 76, 'ending', 'Plan pending.')]]}
    result = lock_schedule(raw, project, lambda *args: None)
    assert [(r['start'], r['end']) for r in result['selections'] if not r['narration']] == [(6, 46)]
