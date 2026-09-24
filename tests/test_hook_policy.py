import copy
import pytest
from backend.hook_policy import source_hook, slots, set_text, VERSION
from backend.plan_first import lock_schedule, plan_first, contract_check
from backend import providers
from backend.story import validate_plan
from backend.timeline import build
from backend.models import Narration, HookAnswer
from test_source_speech import sample
from test_plan_first import environment


def test_real_conflict_hook_keeps_sound_even_if_it_consumes_body_budget():
    p,raw=sample()
    p['hook_policy_version']=VERSION
    p['settings']['original_dialogue_ratio']=.1
    p['source_speech']['items'][1]['hook_score']=.95
    hook=source_hook(p)
    assert (hook['start'],hook['end'])==(14,17)
    out=lock_schedule(raw,p,lambda *a:None)
    assert out['hook']['original_audio']
    assert not out['hook']['narration']
    assert all(x['narration'] for x in out['selections'])
    validate_plan(out,p,check_text=False)


def test_ordinary_dialogue_is_kept_in_body_but_does_not_become_drama_hook():
    p,raw=sample();p['hook_policy_version']=VERSION
    p['source_speech']['items'][1]['hook_score']=.3
    out=lock_schedule(raw,p,lambda *a:None)
    assert not out['hook']['original_audio']
    assert out['hook']['narration']=='__write_hook__'
    assert any(not x['narration'] and x['start']==14 for x in out['selections'])


def test_commentary_and_uncertain_speakers_never_become_original_hook():
    p,_=sample()
    for c in p['source_speech']['items']:c['hook_score']=1
    p['source_speech']['items'][1]['confidence']=.4
    assert source_hook(p) is None


def test_short_scream_can_include_safe_context_without_a_three_second_utterance():
    p,_=sample()
    p['source_speech']['items'][1].update(end=14.6,text='[screaming]',hook_score=.98)
    hook=source_hook(p)
    assert hook is not None and hook['end']-hook['start']==3
    assert hook['start']<=14 and hook['end']>=14.6


def test_context_padding_does_not_include_source_narrator():
    p,_=sample()
    p['source_speech']['items'][1].update(end=14.6,hook_score=.98)
    p['source_speech']['items'][0].update(end=14)
    p['source_speech']['items'][2].update(start=14.6)
    assert source_hook(p) is None


def test_ai_hook_is_timed_generated_and_captioned_from_output_zero(environment):
    p,_,_,_=environment
    out=plan_first(p,lambda *a:None,lambda:None)
    hook=out['story_plan']['hook']
    assert not hook['original_audio'] and hook['narration']!='__write_hook__'
    n=next(n for n in out['narrations'] if n['segment_id']=='hook')
    assert n['section']=='hook' and n['text']==hook['narration']
    assert n['target_duration']==pytest.approx(hook['end']-hook['start']-.04)
    Narration.model_validate(n)
    for row in out['narrations']:
        row.update(audio='voice.wav',duration=row['target_duration'],
                   cues=[dict(id='c',start=0,end=row['target_duration'],text=row['text'])])
        row['audio_hash']=providers.voice_hash(row,out['settings'])
    tl=build(out,strict=True)
    voice=next(v for v in tl['voices'] if v['id']==n['id'])
    assert voice['start']==0 and voice['end']<=tl['clips'][0]['end']
    assert any(c['speaker']=='ai' and c['start']==0 and c['text']==n['text'] for c in tl['cues'])
    assert tl['source_mutes'][0]==dict(start=0,end=4)
    assert tl['duration']==42  # Hook voice does not add playback time.
    contract_check(out)


def test_export_rejects_deleted_or_disabled_ai_hook(environment):
    p,_,_,_=environment
    out=plan_first(p,lambda *a:None,lambda:None)
    out['narrations']=[n for n in out['narrations'] if n['segment_id']!='hook']
    with pytest.raises(ValueError,match='Thiếu lời kể'):
        build(out,strict=True)


def test_hook_repair_sync_does_not_change_geometry_or_other_scenes(environment):
    from backend.plan_first import geometry
    p,_,_,_=environment
    out=plan_first(p,lambda *a:None,lambda:None)
    before=geometry(out['story_plan']);other=copy.deepcopy(out['story_plan']['selections'])
    set_text(out['story_plan'],'hook','A new concise whole-story premise.')
    assert geometry(out['story_plan'])==before
    assert out['story_plan']['selections']==other
    assert slots(out['story_plan'])[0]['narration']=='A new concise whole-story premise.'


def test_fallback_cannot_be_a_silent_hook():
    p,raw=sample();p['hook_policy_version']=VERSION
    out=lock_schedule(raw,p,lambda *a:None)
    out['hook']['narration']=''
    with pytest.raises(ValueError,match='Hook không có tiếng'):
        validate_plan(out,p,check_text=False)


def test_original_hook_never_also_contains_ai_narration():
    p,raw=sample();p['hook_policy_version']=VERSION
    p['source_speech']['items'][1]['hook_score']=1
    out=lock_schedule(raw,p,lambda *a:None)
    out['hook']['narration']='Overlapping narrator'
    with pytest.raises(ValueError,match='chồng thêm'):
        validate_plan(out,p,check_text=False)
