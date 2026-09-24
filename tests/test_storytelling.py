import copy
import json
import pytest
from backend import providers, story, story_schedule
from backend.models import Settings
from backend.timeline import build


def example():
    p = {'id': 'b'*32, 'source': {'file': 'source.mp4'}, 'metadata': {'duration': 100, 'has_audio': True},
         'settings': Settings(output_mode='single', summary_seconds=100, original_dialogue_ratio=.16, narration_style='storytelling', language='English').model_dump(),
         'scenes': [], 'summary': 'Complete situation.', 'hooks': [], 'warnings': [], 'narrations': [],
         'transcript': [{'id': 'c0', 'start': 5, 'end': 6, 'text': 'Muted source'},
                        {'id': 'c1', 'start': 20, 'end': 26, 'text': 'Important exchange'},
                        {'id': 'c2', 'start': 66, 'end': 71, 'text': 'Decisive answer'}]}
    ranges = [(0,20,True), (20,26,False), (26,46,True), (46,66,True), (66,71,False), (71,95,True)]
    raw = {'title':'A situation', 'synopsis':'Events unfold.', 'outcome':'Known ending.', 'lesson':'Evidence matters.',
           'hook': {'start':80,'end':84,'title':'Hook','reason':'A decisive moment'}, 'selections': []}
    for i, (a,b,voiced) in enumerate(ranges):
        raw['selections'].append(dict(start=a,end=b,part=1,section='opening' if i==0 else 'ending' if i==5 else 'development',
            reason='Story progression',priority=.9,evidence='Source dialogue',narration_offset=0,
            narration=('word '*round((b-a)*2.7)).strip() if voiced else ''))
    return p, raw


def test_sparse_commentary_and_excess_original_are_rejected():
    p, raw = example()
    story.validate_plan(raw,p)
    short = copy.deepcopy(raw)
    short['selections'][0]['narration']='A brief reaction.'
    with pytest.raises(ValueError,match='từ/âm tiết'): story.validate_plan(short,p)
    raw['selections'][2]['narration']=''
    with pytest.raises(ValueError,match='Thoại gốc'): story.validate_plan(raw,p)


def test_full_story_plan_maps_only_selected_original_audio_and_captions(monkeypatch):
    p, raw = example()
    p['settings']['duck_volume']=.23
    monkeypatch.setattr(providers,'ask_ai',lambda *a:copy.deepcopy(raw))
    p = story.plan_story(p,lambda *a:None,lambda:None)
    assert p['settings']['duck_volume']==.23
    for n in p['narrations']:
        n.update(duration=n['target_duration'],audio='voice.wav',audio_hash=providers.voice_hash(n,p['settings']))
    t = build(p,strict=True)
    assert t['duration']==99
    assert t['original_audio']==[{'start':0,'end':4},{'start':24,'end':30},{'start':70,'end':75}]
    assert .1 <= t['narration_mix']['original_ratio'] <= .2
    assert .8 <= t['narration_mix']['ai_ratio'] <= .9
    assert 'Muted source' not in [c['text'] for c in t['cues']]
    assert 'Important exchange' in [c['text'] for c in t['cues']]
    assert all(not (c['speaker']=='original' and any(v['start'] < c['end'] and v['end'] > c['start'] for v in t['voices']))
               for c in t['cues'])
    p['settings']['duck_volume']=.4
    assert build(p,strict=True)['planned'] is True
    # Once audio exists, rendering validates real durations, not an estimate
    # recalibrated from a newly created voice reference.
    monkeypatch.setattr(story,'speech_rate',lambda p:5)
    assert build(p,strict=True)['narration_mix']==t['narration_mix']
    p['narrations'][0]['enabled']=False
    with pytest.raises(ValueError,match='Thiếu lời kể'): build(p,strict=True)


def test_ratio_setting_invalidates_plan_and_target_changes_voice_cache(monkeypatch):
    p, raw=example()
    monkeypatch.setattr(providers,'ask_ai',lambda *a:copy.deepcopy(raw))
    p=story.plan_story(p,lambda *a:None,lambda:None)
    n=p['narrations'][0]
    before=providers.voice_hash(n,p['settings'])
    n['target_duration']-=1
    assert providers.voice_hash(n,p['settings'])!=before
    p['settings']['original_dialogue_ratio']=.2
    assert build(p)['planned'] is False


def test_chinese_units_do_not_count_entire_sentence_as_one_word():
    assert story.speech_units('事情发生了变化。')==7


def test_fifty_percent_original_dialogue_passes_plan_and_export_checks(monkeypatch):
    p,raw=example()
    p['metadata']['duration']=60
    p['settings'].update(summary_seconds=60,original_dialogue_ratio=.5)
    raw['hook'].update(start=57,end=60)
    raw['selections']=[]
    ranges=[(0,10,'opening',True),(10,37,'development',False),
            (37,47,'development',True),(47,57,'ending',True)]
    for a,b,section,voiced in ranges:
        raw['selections'].append(dict(start=a,end=b,part=1,section=section,reason='Story',priority=.9,
          evidence='Source dialogue',narration_offset=0,narration=' '.join(['word']*27) if voiced else ''))
    p['transcript']=[dict(id='c1',start=10,end=37,text='Important original dialogue.')]
    monkeypatch.setattr(providers,'ask_ai',lambda *a:copy.deepcopy(raw))
    p=story.plan_story(p,lambda *a:None,lambda:None)
    for n in p['narrations']:
        n.update(duration=n['target_duration'],audio='v.wav',audio_hash=providers.voice_hash(n,p['settings']))
    timeline=build(p,strict=True)
    assert timeline['narration_mix']['original_ratio']==.5
    assert timeline['narration_mix']['ai_ratio']==pytest.approx(.498)
    # The new threshold still rejects missing AI coverage, rather than
    # disabling the check altogether for high original-dialogue ratios.
    p['narrations'][0]['duration']=1
    with pytest.raises(ValueError,match='Lời AI mới phủ'):
        build(p,strict=True)


def test_original_dialogue_setting_accepts_up_to_one_hundred_percent():
    assert Settings(original_dialogue_ratio=.5).original_dialogue_ratio==.5
    assert Settings(original_dialogue_ratio=1).original_dialogue_ratio==1
    with pytest.raises(ValueError):
        Settings(original_dialogue_ratio=1.01)


def _prompt_json(prompt, label):
    start = prompt.index(label) + len(label)
    return json.JSONDecoder().raw_decode(prompt[start:].lstrip())[0]


def test_timed_writer_repairs_only_invalid_slots_and_keeps_valid_prose(monkeypatch, tmp_path):
    p, raw = example()
    monkeypatch.setattr(story, 'speech_rate', lambda project: 2)
    calls = []

    def fake(prompt, *args):
        requested = _prompt_json(prompt, 'REQUESTED SLOTS: ')
        accepted = _prompt_json(prompt, 'ACCEPTED IMMUTABLE LINES: ')
        calls.append((requested, accepted, prompt))
        lines = []
        for position, entry in enumerate(requested):
            # The first response has one valid line and several schema-valid
            # but semantically short lines, matching the production failure.
            words = entry['min_words'] if len(calls) > 1 or position == 0 else entry['min_words'] - 1
            lines.append({'id': entry['id'], 'text': ('word ' * words).strip()})
        return {'items': lines}

    result = story_schedule.write_scheduled(raw, p, fake, tmp_path, lambda *args: None, lambda: None)
    assert len(calls) >= 2
    first_valid = calls[0][0][0]['id']
    assert first_valid not in {entry['id'] for entry in calls[1][0]}
    assert calls[1][1][first_valid] == result['selections'][int(first_valid)]['narration']
    assert all(item['narration'] != '__write__' for item in result['selections'])
    assert [(item['start'], item['end']) for item in result['selections'] if item['narration']]


def test_timed_writer_checkpoint_avoids_poisoned_cache_on_user_retry(monkeypatch, tmp_path):
    p, raw = example()
    monkeypatch.setattr(story, 'speech_rate', lambda project: 2)
    attempts = []

    def always_short_except_first(prompt, *args):
        requested = _prompt_json(prompt, 'REQUESTED SLOTS: ')
        attempts.append(prompt)
        return {'items': [
            {'id': entry['id'],
             'text': ('word ' * (entry['min_words'] if len(attempts) == 1 and position == 0
                                  else entry['min_words'] - 1)).strip()}
            for position, entry in enumerate(requested)
        ]}

    first = story_schedule.write_scheduled(raw, p, always_short_except_first, tmp_path,
                                           lambda *args: None, lambda: None)
    assert len(first['selections']) >= len(raw['selections'])

    retried = []
    def corrected(prompt, *args):
        requested = _prompt_json(prompt, 'REQUESTED SLOTS: ')
        retried.append(prompt)
        return {'items': [{'id': entry['id'], 'text': ('word ' * entry['min_words']).strip()}
                          for entry in requested]}

    result = story_schedule.write_scheduled(raw, p, corrected, tmp_path, lambda *args: None, lambda: None)
    assert 'REPAIR GENERATION: 7' in retried[0]
    assert result['selections'][0]['narration'] == ('word ' * 32).strip()


def test_timed_writer_ignores_corrupt_checkpoint_and_propagates_provider_error(monkeypatch, tmp_path):
    p, raw = example()
    monkeypatch.setattr(story, 'speech_rate', lambda project: 2)

    def auth_failure(*args):
        raise RuntimeError('provider authentication failed')

    with pytest.raises(RuntimeError, match='authentication'):
        story_schedule.write_scheduled(raw, p, auth_failure, tmp_path,
                                       lambda *args: None, lambda: None)
    checkpoint = next((tmp_path / 'story-schedule-cache').glob('*.json'))
    checkpoint.write_text('[]', 'utf-8')

    def corrected(prompt, *args):
        requested = _prompt_json(prompt, 'REQUESTED SLOTS: ')
        return {'items': [{'id': entry['id'], 'text': ('word ' * entry['min_words']).strip()}
                          for entry in requested]}

    result = story_schedule.write_scheduled(raw, p, corrected, tmp_path, lambda *args: None, lambda: None)
    assert result['selections'][0]['narration'] == ('word ' * 32).strip()
