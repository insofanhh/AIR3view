import copy
import pytest
from backend.scene_duration_repair import adjust
from backend.story import plan_fingerprint,duration_plan_manifest,validate_plan
from backend.plan_first import geometry,contract_check
from backend.retention import budget
from backend.source_policy import VERSION
from backend.voice_repair import repair_text
from test_scene_duration_repair import project


def ratio_project(tmp_path,monkeypatch):
    p=project(tmp_path,monkeypatch)
    p['settings'].update(original_dialogue_ratio=.7,summary_seconds=100,duration_min_ratio=.9)
    p['metadata'].update(duration=160,has_audio=True)
    p.update(retention_policy_version=1,source_policy_version=VERSION)
    hook=dict(start=120,end=124,title='Hook',reason='People talking',original_audio=True,narration='')
    p['settings'].update(hook_start=120,hook_end=124,hook_enabled=True)
    rows=[]
    for i,(a,b,voiced,section) in enumerate([(0,10,True,'opening'),(10,76,False,'development'),(76,90,True,'development'),(90,96,True,'ending')]):
        rows.append(dict(id=f'sel{i}',start=a,end=b,part=1,section=section,reason='Events',priority=.8,
                         evidence='Real exchange',narration='Narrator describes the events.' if voiced else '',narration_offset=0))
    p['story_plan'].update(hook=hook,selections=rows)
    p['source_speech']=dict(version=VERSION,items=[dict(cue=i,start=a,end=b,text='Real conversation.',role='participant',confidence=.95,priority=.8)
        for i,(a,b) in enumerate([(10,12),(12,76),(120,124)])])
    p['transcript']=[dict(id=str(i),start=c['start'],end=c['end'],text=c['text']) for i,c in enumerate(p['source_speech']['items'])]
    p['narrations']=[dict(id='story-'+r['id'],segment_id=r['id'],start=r['start'],text=r['narration'],evidence=r['evidence'],
        enabled=True,part=1,section=r['section'],target_duration=round(r['end']-r['start']-.04,3),audio='',audio_hash='',duration=0,cues=[],caption_version=0)
        for r in rows if r['narration']]
    p['plan_fingerprint']=plan_fingerprint(p)
    p['duration_plan'].update(duration_plan_manifest(p['story_plan'],p),schedule=copy.deepcopy(p['story_plan']),
                              geometry=geometry(p['story_plan']),input_fingerprint=p['plan_fingerprint'])
    contract_check(p)
    return p


def test_shrinking_ai_balances_original_ratio_at_real_cue_boundary(tmp_path,monkeypatch):
    p=ratio_project(tmp_path,monkeypatch);before=copy.deepcopy(p)
    result=adjust(p,p['narrations'][-1],5.2,5.96)
    assert result is not None and p==before
    info=budget(result['story_plan'],result)
    assert .67<=info['actual_ratio']<=.7
    assert 90<=info['total_seconds']<=100
    change=result['voice_repair_state']['story-sel3']['scene_adjustments'][-1]
    assert change['ratio_compensation']['new_start']==12
    assert result['story_plan']['selections'][0]==p['story_plan']['selections'][0]
    contract_check(result)


def test_cannot_balance_by_cutting_arbitrary_original_words(tmp_path,monkeypatch):
    p=ratio_project(tmp_path,monkeypatch)
    p['source_speech']['items']=p['source_speech']['items'][1:]
    p['source_speech']['items'][0]['start']=10
    # One unbroken utterance: no safe internal boundary to trim. Neighbour
    # narration is marked complete, so cannot be borrowed from either.
    p['narrations'][-2]['audio']='done.wav'
    diagnostics=[]
    assert adjust(p,p['narrations'][-1],5.2,5.96,diagnostics) is None
    assert any('Thoại gốc' in reason for reason in diagnostics)


def test_no_shrink_below_minimum_to_fix_ratio(tmp_path,monkeypatch):
    p=ratio_project(tmp_path,monkeypatch);p['settings']['duration_min_ratio']=1
    p['plan_fingerprint']=plan_fingerprint(p);p['duration_plan']['input_fingerprint']=p['plan_fingerprint']
    p['narrations'][-2]['audio']='done.wav'
    assert adjust(p,p['narrations'][-1],5.2,5.96) is None


def test_valid_rewrite_can_be_measured_instead_of_failing_word_estimate(tmp_path):
    text='Trish Whailing reflects on the profound personal impact of losing her beloved daughter Jordan to a drunk driver.'
    shorter='Trish Whailing recalls losing her daughter Jordan and describes how the tragedy affected her family.'
    calls=[]
    def ask(prompt,*a):calls.append(prompt);return {'text':shorter}
    out=repair_text(text,'Same events.','English',5.727,5.2,
        {'production_workflow':'plan_first','original_dialogue_ratio':.7},tmp_path,lambda:None,ask,
        max_attempts=3,measured_trial=True)
    assert out==shorter and len(calls)==3


def test_measured_trial_never_returns_a_previously_measured_miss(tmp_path):
    text='Trish Whailing reflects on the profound personal impact of losing her beloved daughter Jordan to a drunk driver.'
    shorter='Trish Whailing recalls losing her daughter Jordan and describes how the tragedy affected her family.'
    with pytest.raises(ValueError):
        repair_text(text,'Evidence','English',5.727,5.2,{'production_workflow':'plan_first'},tmp_path,lambda:None,
                    lambda *a:{'text':shorter},max_attempts=1,measured_trial=True,
                    history=[dict(text=shorter,measured=4,target=5.727)])


@pytest.mark.parametrize('bad',['What are you doing to me?','One. Two. Three.',''])
def test_trial_does_not_bypass_content_guards(tmp_path,bad):
    with pytest.raises(ValueError):
        repair_text('The narrator describes the crash and its consequences.','Evidence','English',8,4,
            {'production_workflow':'plan_first','original_dialogue_ratio':.7},tmp_path,lambda:None,
            lambda *a:{'text':bad},measured_trial=True,max_attempts=1)
