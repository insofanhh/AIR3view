import copy
import pytest
from backend import providers, story
from backend.models import Settings
from backend.timeline import build


def example():
    p = {'id': 'b'*32, 'source': {'file': 'source.mp4'}, 'metadata': {'duration': 100, 'has_audio': True},
         'settings': Settings(output_mode='single', summary_seconds=100, narration_style='storytelling', language='English').model_dump(),
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
