import copy
import pytest
from backend.retention import VERSION, budget, runs, validate, instructions
from backend.source_speech import allowed
from backend.source_policy import VERSION as SOURCE_VERSION
from backend.story_schedule import schedule
from backend.story import validate_plan
from backend.models import Settings
from test_plan_first import environment
from backend.plan_first import plan_first
from backend.source_speech import SpeechRoles
from backend import providers


def rolling_source():
    p=dict(metadata={'duration':200,'has_audio':True},settings=Settings(output_mode='single',summary_seconds=100,
        language='English',narration_style='storytelling',original_dialogue_ratio=.7).model_dump(),
        transcript_origin='youtube_auto_subtitles',retention_policy_version=VERSION,
        source_speech={'version':SOURCE_VERSION,'items':[]},transcript=[])
    for i in range(48):
        row=dict(cue=i,start=i*2,end=i*2+4,text='People speaking to each other.',role='participant',confidence=.95,priority=.8)
        p['source_speech']['items'].append(row)
        p['transcript'].append({**row,'id':str(i)})
    p['source_speech']['items'].append(dict(cue=99,start=98,end=104,role='commentary',confidence=.9,priority=.1,text='Source narrator.'))
    raw=dict(title='Story',synopsis='Context',outcome='Known',lesson='Evidence',
        hook=dict(start=150,end=154,title='Hook',reason='Whole story'),selections=[])
    for a,b,section in [(0,20,'opening'),(20,80,'development'),(80,96,'ending')]:
        raw['selections'].append(dict(start=a,end=b,section=section,part=1,narration='Pending',narration_offset=0,priority=.9,evidence='Source events',reason='Story'))
    return p,raw


@pytest.mark.parametrize('ratio',[.6,.7,.8])
def test_targets_are_measured_before_tts_despite_rolling_caption_overlap(ratio):
    p,raw=rolling_source();p['settings']['original_dialogue_ratio']=ratio
    result=schedule(raw,p)
    validate_plan(result,p,check_text=False)
    info=budget(result,p)
    assert ratio-.03<=info['actual_ratio']<=ratio+.001
    assert 1-ratio-.001<=info['ai_planned_ratio']<=1-ratio+.03
    assert info['total_seconds']==100


def test_rolling_captions_allow_observed_boundaries_but_not_commentary():
    p,_=rolling_source()
    assert allowed(p,10,30)
    assert not allowed(p,10.7,30.7)
    assert not allowed(p,94,100)
    p['transcript_origin']='imported_srt'
    assert not allowed(p,10,30)  # Don't relax boundaries for independent cues.


def test_overlaps_are_not_double_counted_and_commentary_is_subtracted():
    p,_=rolling_source()
    assert sum(g['end']-g['start'] for g in runs(p))==98
    p['source_speech']['items'].append(dict(start=40,end=50,role='unknown',confidence=.9,text='Mixed'))
    assert sum(g['end']-g['start'] for g in runs(p))==88


def test_five_percent_original_is_rejected_when_source_can_meet_seventy():
    p,raw=rolling_source()
    raw['hook']['original_audio']=False
    with pytest.raises(ValueError,match='ORIGINAL_AUDIO_TARGET'):
        validate(raw,p)


def test_real_shortage_reports_effective_seconds_without_copying_commentary():
    p,raw=rolling_source()
    for c in p['source_speech']['items']:
        if c.get('cue')!=0:c['role']='commentary'
    # Keep the sole unambiguous two-second part; other speech overlaps
    # excluded commentary and must never be counted as real original.
    result=schedule(raw,p)
    info=budget(result,p)
    assert info['shortfall_seconds']>60
    assert info['effective_seconds']<=4
    assert info['available_seconds']<=4


def test_wrong_first_footage_replanned_before_writing(environment,monkeypatch):
    from backend.plan_first import FootagePlan
    from backend.story_schedule import ScheduledNarration
    p,footage,calls,ask=environment
    p['metadata']['has_audio']=True;p['settings']['original_dialogue_ratio']=.7
    p['transcript']=[dict(id=str(i),start=i,end=i+1,text='Actual exchange') for i in range(40)]
    footage['selections'][-1].update(start=30,end=40)
    attempt=[0]
    def provider(prompt,*args):
        if args[-1] is FootagePlan:
            attempt[0]+=1
            if attempt[0]==1:
                bad=copy.deepcopy(footage)
                for row,(a,b) in zip(bad['selections'],[(45,55),(55,73),(80,90)]):row.update(start=a,end=b)
                return bad
            assert 'ORIGINAL_AUDIO_TARGET' in prompt
            assert 'Target real source audio 70%' in prompt
        return ask(prompt,*args)
    monkeypatch.setattr(providers,'ask_ai',provider)
    result=plan_first(p,lambda *a:None,lambda:None)
    assert attempt[0]==2
    info=result['duration_plan']['retention']
    assert info['actual_seconds']+info['tolerance_seconds']>=info['effective_seconds']


def test_target_prompt_keeps_outcome_and_does_not_request_commentary_padding():
    p,_=rolling_source()
    prompt=instructions(p)
    assert 'BEFORE choosing narrated footage' in prompt
    assert 'actual outcome' in prompt and 'never pad with commentary' in prompt
