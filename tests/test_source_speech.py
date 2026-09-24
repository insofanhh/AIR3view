import copy
import json
import pytest
from backend import source_speech as speech
from backend.media import Cancelled
from backend.models import Settings
from backend.plan_first import lock_schedule
from backend.source_policy import VERSION
from backend.story import validate_plan, plan_fingerprint
from backend.timeline import build


def sample():
    p = dict(id='e'*32,source={'file':'source.mp4'},metadata={'duration':100,'has_audio':True},
             settings=Settings(output_mode='single',summary_seconds=45,original_dialogue_ratio=.5,
                               narration_style='storytelling',language='English').model_dump(),
             summary='The participant refuses a request.',scenes=[],narrations=[],warnings=[],transcript=[])
    rows=[(1,5,'commentary','External introduction'),(14,17,'participant','No, I will not do that.'),
          (18,25,'commentary','External account of the event'),(50,55,'unknown','Unclear speaker'),
          (80,84,'commentary','Source AI recap')]
    p['source_speech']=dict(version=VERSION,items=[dict(cue=i,start=a,end=b,role=role,text=text,
        confidence=.95,priority=.9,evidence='Supported role') for i,(a,b,role,text) in enumerate(rows)])
    p['transcript']=[dict(id=str(x['cue']),**{k:x[k] for k in ('start','end','text')}) for x in p['source_speech']['items']]
    raw=dict(title='Events',synopsis='Events',outcome='Refusal',lesson='Context',
             hook=dict(start=80,end=84,title='Moment',reason='Action'),selections=[])
    for a,b,section in [(0,10,'opening'),(10,28,'development'),(50,60,'ending')]:
        raw['selections'].append(dict(start=a,end=b,part=1,section=section,reason='Evidence',priority=.9,
            evidence='Source',narration='Pending',narration_offset=0))
    return p,raw


def test_sparse_dialogue_is_retained_at_seven_percent_with_fifty_percent_cap():
    p,raw=sample()
    out=lock_schedule(raw,p,lambda *a:None)
    assert [(x['start'],x['end']) for x in out['selections'] if not x['narration']]==[(14,17)]
    assert not out['hook']['original_audio']
    assert sum(x['end']-x['start'] for x in out['selections'])==38
    validate_plan(out,p,check_text=False)


def test_all_commentary_becomes_narrated_footage_without_quota_failure():
    p,raw=sample()
    for c in p['source_speech']['items']:c['role']='commentary'
    out=lock_schedule(raw,p,lambda *a:None)
    assert all(x['narration'] for x in out['selections'])
    assert not out['hook']['original_audio']


def test_missing_sparse_exchange_requires_replan_instead_of_silent_loss():
    p,raw=sample()
    raw['selections'][1].update(start=30,end=48)
    with pytest.raises(ValueError,match='hội thoại thật đã ưu tiên'):
        lock_schedule(raw,p,lambda *a:None)


def test_validator_rejects_commentary_marked_original():
    p,raw=sample()
    out=lock_schedule(raw,p,lambda *a:None)
    next(x for x in out['selections'] if x['start']==17)['narration']=''
    with pytest.raises(ValueError,match='hội thoại thật'):
        validate_plan(out,p,check_text=False)


def test_overlapping_commentary_excludes_a_participant_candidate():
    p,_=sample()
    p['source_speech']['items'].append(dict(start=15,end=16,role='commentary',confidence=.9))
    assert not speech.allowed(p,14,17)
    assert speech.anchor(p) is None


def test_timeline_mutes_commentary_and_does_not_caption_it():
    p,raw=sample()
    p['story_plan']=lock_schedule(raw,p,lambda *a:None)
    p['settings'].update(hook_enabled=True,hook_start=80,hook_end=84)
    p['plan_fingerprint']=plan_fingerprint(p)
    tl=build(p)
    assert tl['original_audio']==[dict(start=18,end=21)]
    assert tl['narration_mix']['original_ratio']==pytest.approx(3/42)
    for time in (1,7,24,36):
        assert any(x['start']<=time<x['end'] for x in tl['source_mutes'])
    assert not any(x['start']<=19<x['end'] for x in tl['source_mutes'])
    assert [c['text'] for c in tl['cues']]==['No, I will not do that.']


def classify_reply(prompt):
    rows=json.loads(prompt.split('REQUESTED CUES: ',1)[1].split('\nREPAIR',1)[0])
    return {'items':[dict(cue=c['cue'],role='participant' if c['start']==14 else 'commentary',
                         confidence=.9,priority=.8,evidence='Turn context') for c in rows]}


def test_role_cache_survives_duration_changes_and_invalidates_changed_text(tmp_path):
    p,_=sample();p.pop('source_speech')
    calls=[]
    def ask(prompt,*a):
        calls.append(prompt)
        return classify_reply(prompt)
    result=speech.classify(p,ask,tmp_path,lambda *a:None,lambda:None)
    p['settings']['summary_seconds']=60
    assert speech.classify(p,ask,tmp_path,lambda *a:None,lambda:None)==result
    assert len(calls)==1
    p['transcript'][0]['text']='New evidence'
    speech.classify(p,ask,tmp_path,lambda *a:None,lambda:None)
    assert len(calls)==2


def test_incomplete_roles_retry_and_never_treat_missing_as_real(tmp_path):
    p,_=sample();calls=[]
    def ask(prompt,*a):
        calls.append(prompt)
        return {'items':[]}
    with pytest.raises(ValueError,match='thiếu/lặp'):
        speech.classify(p,ask,tmp_path,lambda *a:None,lambda:None)
    assert len(calls)==4
    assert not list((tmp_path/'source-speech-cache').glob('*.json'))


def test_extra_context_ids_are_filtered_and_valid_batch_is_cached(tmp_path):
    p,_=sample();calls=[]
    def ask(prompt,*a):
        calls.append(prompt)
        rows=json.loads(prompt.split('REQUESTED CUES: ',1)[1].split('\nREPAIR',1)[0])
        return {'items':[dict(cue=c['cue'],role='participant',confidence=.95,priority=.8,evidence='Role') for c in rows]
                       +[dict(cue=999,role='commentary',confidence=.95,priority=.1,evidence='Context only')]}
    result=speech.classify(p,ask,tmp_path,lambda *a:None,lambda:None)
    assert len(calls)==1
    assert len(result['items'])==len(p['transcript'])
    assert all(item['cue']!=999 for item in result['items'])
    cached=list((tmp_path/'source-speech-cache').glob('*.json'))
    assert cached and len(json.loads(cached[0].read_text('utf-8'))['items'])==len(p['transcript'])



@pytest.mark.parametrize('error',[RuntimeError('API unavailable'),Cancelled('cancelled')])
def test_role_provider_errors_do_not_turn_into_all_commentary(tmp_path,error):
    p,_=sample()
    def ask(*a):raise error
    with pytest.raises(type(error)):
        speech.classify(p,ask,tmp_path,lambda *a:None,lambda:None)


def test_small_cap_prioritizes_exchange_over_repeated_hook():
    p,raw=sample();p['settings']['original_dialogue_ratio']=.1
    raw['hook'].update(start=14,end=17)
    result=lock_schedule(raw,p,lambda *a:None)
    assert not result['hook']['original_audio']
    assert [(x['start'],x['end']) for x in result['selections'] if not x['narration']]==[(14,17)]


def test_adjacent_long_group_cannot_swallow_short_exchange():
    p,raw=sample();p['settings']['original_dialogue_ratio']=.1
    p['source_speech']['items'][2].update(start=17,end=25,role='participant')
    out=lock_schedule(raw,p,lambda *a:None)
    assert [(x['start'],x['end']) for x in out['selections'] if not x['narration']]==[(14,17)]


def test_mute_padding_does_not_cut_adjacent_real_exchange():
    p,_=sample()
    p['source_speech']['items'][0]['end']=14
    p['source_speech']['items'][2]['start']=17
    ranges=speech.muted_ranges(p)
    assert not any(a<17 and b>14 for a,b in ranges)


def test_hook_schema_requires_audio_flag_but_accepts_old_saved_hook():
    from backend.models import HookAnswer
    schema=HookAnswer.model_json_schema()
    assert set(schema['required'])==set(schema['properties'])
    assert 'default' not in schema['properties']['original_audio']
    assert HookAnswer(start=0,end=3,title='Hook',reason='Evidence').original_audio
