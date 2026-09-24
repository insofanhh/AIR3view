import copy
import pytest
from backend import providers
from backend.plan_first import plan_first, lock_schedule, contract_check
from backend.source_speech import SpeechRoles
from backend.story_schedule import ScheduledNarration
from backend.story import validate_plan
from backend.timeline import build
from test_plan_first import environment
from test_source_speech import sample


def real_dialogue(environment,monkeypatch,ratio=1):
    p,footage,calls,ask=environment
    p['metadata']['has_audio']=True
    p['settings']['original_dialogue_ratio']=ratio
    p['transcript']=[dict(id=f'c{i}',start=a,end=b,text=text) for i,(a,b,text) in enumerate([
        (0,10,'The people establish the situation.'),
        (10,13,'Stop! Leave me alone!'),
        (13,28,'They explain the dispute directly to one another.'),
        (50,60,'They confirm the resolution.')])]
    def classified(prompt,*args):
        result=ask(prompt,*args)
        if args[-1] is SpeechRoles:
            for item in result['items']:
                item['hook_score']=.95 if item['cue']==1 else 0
        return result
    monkeypatch.setattr(providers,'ask_ai',classified)
    return p,calls


def test_one_hundred_can_keep_entire_real_story_without_tts(environment,monkeypatch):
    p,calls=real_dialogue(environment,monkeypatch)
    result=plan_first(p,lambda *a:None,lambda:None)
    assert result['story_plan']['hook']['original_audio']
    assert not result['narrations']
    assert all(not x['narration'] for x in result['story_plan']['selections'])
    assert ScheduledNarration not in calls
    contract_check(result)
    timeline=build(result,strict=True)
    assert timeline['narration_mix']['original_ratio']==pytest.approx(1)
    assert timeline['narration_mix']['ai_ratio']==0
    assert timeline['duration']==41
    import gradio_client
    monkeypatch.setattr(gradio_client,'Client',lambda *a,**kw:pytest.fail('No TTS connection for an all-original plan'))
    assert providers.synthesize(result,lambda *a:None,lambda:None) is result


def test_increasing_cap_reduces_ai_without_changing_video_duration(environment,monkeypatch):
    p,_=real_dialogue(environment,monkeypatch,ratio=.5)
    low=plan_first(p,lambda *a:None,lambda:None)
    p['settings']['original_dialogue_ratio']=1
    high=plan_first(p,lambda *a:None,lambda:None)
    assert sum(n['target_duration'] for n in high['narrations'])<sum(n['target_duration'] for n in low['narrations'])
    assert build(low)['duration']==build(high)['duration']
    assert build(high)['narration_mix']['original_ratio']>build(low)['narration_mix']['original_ratio']


def test_high_cap_never_reclassifies_source_commentary_as_real_dialogue():
    p,raw=sample();p['settings']['original_dialogue_ratio']=1
    result=lock_schedule(raw,p,lambda *a:None)
    originals=[(x['start'],x['end']) for x in result['selections'] if not x['narration']]
    assert originals==[(14,17)]
    assert result['hook']['narration'] and not result['hook']['original_audio']
    validate_plan(result,p,check_text=False)


def test_high_cap_without_participant_speech_still_uses_ai():
    p,raw=sample();p['settings']['original_dialogue_ratio']=1
    for c in p['source_speech']['items']:c['role']='commentary'
    result=lock_schedule(raw,p,lambda *a:None)
    assert all(x['narration'] for x in result['selections'])
    assert result['hook']['narration']


def test_high_retention_preserves_long_exchange_instead_of_one_short_cue():
    p,raw=sample();p['settings']['original_dialogue_ratio']=1
    p['source_speech']['items']=[dict(cue=i,start=a,end=b,text='Real exchange',role='participant',
        confidence=.95,priority=.9,evidence='Actual people talking') for i,(a,b) in enumerate([(10,13),(13.2,18),(18.2,28)])]
    p['transcript']=[dict(id=str(i),start=c['start'],end=c['end'],text=c['text']) for i,c in enumerate(p['source_speech']['items'])]
    result=lock_schedule(raw,p,lambda *a:None)
    assert [(x['start'],x['end']) for x in result['selections'] if not x['narration']]==[(10,28)]


def test_high_retention_requires_ai_bridge_in_each_story_section():
    p,raw=sample();p['settings']['original_dialogue_ratio']=.7
    for row in raw['selections']:row['narration']=''
    p['story_bridge_version']=1
    with pytest.raises(ValueError,match='lời AI ngắn xen kẽ|STORY_STRUCTURE'):
        validate_plan(raw,p,check_text=False)


def test_high_retention_ai_slots_are_short_bridge_chunks():
    p,raw=sample();p['settings']['original_dialogue_ratio']=.7
    p['story_bridge_version']=1
    result=copy.deepcopy(raw)
    result['selections']=[
        {**result['selections'][0],'start':0,'end':8,'narration':'Context.'},
        {**result['selections'][1],'start':8,'end':12,'narration':''},
        {**result['selections'][1],'start':12,'end':20,'narration':'The exchange escalates.'},
        {**result['selections'][1],'start':20,'end':24,'narration':''},
        {**result['selections'][2],'start':24,'end':32,'narration':'The outcome is confirmed.'},
    ]
    from backend.story_bridges import validate
    validate(result,p,check_text=True)
    assert all('.' in x['narration'] and len(x['narration'].split())<8 for x in result['selections'] if x['narration'])
