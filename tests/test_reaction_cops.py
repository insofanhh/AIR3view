import copy
import json

import pytest

from backend.models import Settings
from backend.reaction_cops import (_planner_catalog, _clean_footage, _effective_duration,
                                   _fill_from_full_source, optimize_cues, validate_plan)
from backend.timeline import build_story
from backend.voice_repair import source_evidence


def fixture():
    settings = Settings(output_mode='single', summary_seconds=30, duration_min_ratio=.6,
                        production_workflow='plan_first', narration_style='storytelling',
                        editorial_mode='reaction_cops', original_dialogue_ratio=.5).model_dump()
    cues = [dict(id=str(i), start=i*10.0, end=(i+1)*10.0, text=f'Scene exchange {i}',
                 speaker='original', words=[]) for i in range(3)]
    roles = [dict(cue=i, start=c['start'], end=c['end'], text=c['text'], role='participant',
                  confidence=.9, priority=.8, evidence='real conversation', hook_score=0)
             for i,c in enumerate(cues)]
    project = {'settings': settings, 'metadata': {'duration':30.0,'has_audio':True},
               'source_speech': {'version':3,'items':roles},
               'reaction_cues': [{**c,'source_cue_ids':[c['id']]} for c in cues],
               'transcript':cues, 'narrations':[], 'hook_policy_version':1,
               'retention_policy_version':1}
    selections=[]
    for i,c in enumerate(cues):
        selections.append(dict(start=c['start'],end=c['end'],part=1,
            section='development',reason='confirmed turn',priority=.8,
            evidence='SRC_CUES='+json.dumps([c['id']])+'\n'+c['text'],
            narration='' if i==1 else 'Commentary point',narration_offset=0))
    plan = {'title':'Reaction','synopsis':'Confirmed exchanges',
            'outcome':'Last confirmed exchange','lesson':'No spoken lesson',
            'hook':{'start':0,'end':0,'title':'','reason':'Hook off','original_audio':False,'narration':''},
            'selections':selections}
    return project,plan


def test_no_hook_has_no_phantom_clip_or_intro_outro():
    project,plan=fixture()
    accepted=validate_plan(plan,project,check_text=False)
    project['story_plan']=accepted
    timeline=build_story(project,strict=False)
    assert timeline['duration']==30
    assert all(c['kind']!='hook' for c in timeline['clips'])
    assert len([s for s in accepted['selections'] if s['narration']])==2


def test_source_narrator_cannot_support_commentary():
    project,plan=fixture()
    project['source_speech']['items'][2]['role']='commentary'
    with pytest.raises(ValueError,match='not_enough_evidence'):
        validate_plan(plan,project,check_text=False)


def test_mixed_cue_cannot_be_retained_as_original():
    project,plan=fixture()
    project['source_speech']['items'][1]['role']='unknown'
    with pytest.raises(ValueError,match='not_enough_evidence'):
        validate_plan(plan,project,check_text=False)


def test_hook_requires_explicit_toggle_and_real_source():
    project,plan=fixture()
    plan=copy.deepcopy(plan)
    plan['hook'].update(start=0,end=3,original_audio=False,narration='AI premise')
    with pytest.raises(ValueError,match='Hook'):
        validate_plan(plan,project,check_text=False)
    project['settings']['hook_enabled']=True
    project['settings']['summary_seconds']=35
    with pytest.raises(ValueError,match='Hook'):
        validate_plan(plan,project,check_text=False)


def test_real_source_hook_adds_only_its_measured_duration():
    project,plan=fixture()
    project['settings']['hook_enabled']=True
    project['settings']['summary_seconds']=33
    project['reaction_cues'][0]['end']=3
    project['source_speech']['items'][0]['end']=3
    plan['hook'].update(start=0,end=3,original_audio=True,narration='')
    accepted=validate_plan(plan,project,check_text=False)
    project['story_plan']=accepted
    timeline=build_story(project,strict=False)
    assert timeline['duration']==33
    assert timeline['clips'][0]['kind']=='hook'
    assert timeline['clips'][0]['end']==3


def test_time_citation_is_not_spoken():
    project,plan=fixture()
    plan['selections'][0]['narration']='At 12 seconds, an officer asks a question.'
    with pytest.raises(ValueError,match='mốc thời gian'):
        validate_plan(plan,project,check_text=False)


def test_one_clean_exchange_can_support_one_commentary_point():
    project,plan=fixture()
    project['settings']['summary_seconds']=10
    project['metadata']['duration']=10
    project['source_speech']['items']=project['source_speech']['items'][:1]
    project['reaction_cues']=project['reaction_cues'][:1]
    project['transcript']=project['transcript'][:1]
    plan['selections']=plan['selections'][:1]
    assert len(validate_plan(plan,project,check_text=False)['selections'])==1


def test_unsafe_srt_merge_falls_back_to_lossless_raw_cues():
    project,_=fixture()
    project['source_transcript']=project['transcript'][:2]
    def invalid_optimizer(*_args):
        return {'cues':[{'source_cue_ids':['0'],'text':'changed'}], 'warnings':[]}
    result=optimize_cues(project,invalid_optimizer,None,lambda *_:None,lambda:None)
    assert [c['source_cue_ids'] for c in result['cues']]==[['0'],['1']]
    assert [c['text'] for c in result['cues']]==[c['text'] for c in project['source_transcript']]


def test_srt_token_exhaustion_splits_batch_and_preserves_every_cue():
    from backend.providers import OpenAIOutputIncomplete
    project,_=fixture()
    project['source_transcript']=[dict(id=str(i),start=float(i),end=i+.5,
                                       text=f'Scene exchange {i}') for i in range(24)]
    project['settings']['provider']='openai'
    calls=[]

    def optimizer(prompt,_images,_settings,_folder,_check,_schema):
        requested=json.loads(prompt.split('\nREQUESTED: ',1)[1])
        calls.append(len(requested))
        if len(requested)>12:
            raise OpenAIOutputIncomplete('max_output_tokens',{'reasoning_tokens':2500})
        return {'cues':[{'source_cue_ids':[c['id']],'text':c['text']} for c in requested],
                'warnings':[]}

    result=optimize_cues(project,optimizer,None,lambda *_:None,lambda:None)
    assert calls==[24,12,12]
    assert [c['source_cue_ids'][0] for c in result['cues']]==[str(i) for i in range(24)]


def test_srt_token_exhaustion_uses_raw_cues_after_bounded_splits():
    from backend.providers import OpenAIOutputIncomplete
    project,_=fixture()
    project['source_transcript']=[dict(id=str(i),start=float(i),end=i+.5,
                                       text=f'Scene exchange {i}') for i in range(24)]
    project['settings']['provider']='openai'
    calls=[]

    def optimizer(prompt,*_args):
        requested=json.loads(prompt.split('\nREQUESTED: ',1)[1])
        calls.append(len(requested))
        raise OpenAIOutputIncomplete('max_output_tokens')

    result=optimize_cues(project,optimizer,None,lambda *_:None,lambda:None)
    assert calls==[24,12,6,6,12,6,6]
    assert [c['text'] for c in result['cues']]==[c['text'] for c in project['source_transcript']]
    assert len(project['warnings'])==4


def test_duration_repair_receives_only_cited_in_scene_evidence():
    project,plan=fixture()
    project['summary']='Narrator claims a later arrest'
    project['reaction_cues'].append({'id':'3','source_cue_ids':['3'],'start':2,'end':4,
                                     'text':'Narrator claims a later arrest'})
    project['source_speech']['items'].append({'cue':3,'start':2,'end':4,'text':'Narrator claims a later arrest',
        'role':'commentary','confidence':.99,'priority':0,'evidence':'voice-over','hook_score':0})
    evidence=source_evidence(project,{'evidence':plan['selections'][0]['evidence'],'start':0,'target_duration':10})
    assert 'Scene exchange 0' in evidence
    assert 'later arrest' not in evidence


def test_reaction_plan_first_pipeline_keeps_hook_off_and_only_eligible_cues(monkeypatch,tmp_path):
    from backend import providers, store
    from backend.reaction_cops import plan_reaction
    project, _ = fixture()
    project.update(id='reaction-test',source='fixture.mp4',summary='Unverified narrator claim',
                   scenes=[],hooks=[],exports=[],preview_exports=[],warnings=[])
    project['settings']['provider']='openai'
    project['source_transcript']=project['transcript']
    monkeypatch.setattr(store,'project_dir',lambda _id:tmp_path)
    seen=[]
    plan_calls=[]
    def fake_ai(prompt,_frames,_settings,_folder,_check,schema):
        seen.append((schema.__name__,prompt))
        if schema.__name__=='OptimizedBatch':
            return {'cues':[{'source_cue_ids':[c['id']],'text':c['text']} for c in project['transcript']],
                    'warnings':[]}
        if schema.__name__=='SpeechRoles':
            return {'items':[{'cue':i,'role':'participant','confidence':.9,'priority':.8,
                              'evidence':'direct exchange','hook_score':0} for i in range(3)]}
        if schema.__name__=='ReactionFootagePlan':
            plan_calls.append(1)
            if len(plan_calls)==1:
                raise providers.OpenAIRequestTooLarge(10000,11977)
            return {'title':'Reaction','synopsis':'Confirmed exchanges','last_confirmed_event':'Third exchange',
                    'selections':[{'start':i*10,'end':(i+1)*10,'part':1,'section':'development',
                                   'reason':'confirmed exchange','priority':.8,'source_cue_ids':[str(i)],
                                   'keep_original':i==1} for i in range(3)]}
        if schema.__name__=='ScheduledNarration':
            return {'items':[{'id':str(i),'text':' '.join(['diễn biến']*36)} for i in (0,2)]}
        raise AssertionError(schema.__name__)
    monkeypatch.setattr(providers,'ask_ai',fake_ai)
    result=plan_reaction(project,lambda *_:None,lambda:None)
    assert result['story_plan']['hook']['end']==0
    assert result['settings']['hook_enabled'] is False
    assert len(result['narrations'])==2
    assert result['duration_plan']['status']=='ready'
    assert len(plan_calls)==2
    assert 'Unverified narrator claim' not in next(p for name,p in seen if name=='ReactionFootagePlan')
    assert 'Unverified narrator claim' not in next(p for name,p in seen if name=='ScheduledNarration')


def test_long_source_planner_catalog_is_bounded_and_spans_source():
    catalog=[{'id':str(i),'start':i*10,'end':i*10+2,'priority':(i%7)/7,
              'text':'x'*500} for i in range(1000)]
    picked=_planner_catalog(catalog,10000)
    assert len(picked)<=82
    assert picked[0]['id']=='0' and picked[-1]['id']=='999'
    assert all(len(c['text'])<=220 for c in picked)
    assert len({int(c['start']//1000) for c in picked})==10


def _many_clean_cues(count, requested=240):
    project,_=fixture()
    project['settings'].update(summary_seconds=requested, duration_min_ratio=.9,
                               original_dialogue_ratio=.7)
    cues=[dict(id=str(i), start=round(i*2.1, 3), end=round(i*2.1+2, 3),
               text=f'Confirmed in-scene exchange {i}', source_cue_ids=[str(i)], priority=.8)
          for i in range(count)]
    project['reaction_cues']=cues
    project['transcript']=cues
    project['metadata']['duration']=cues[-1]['end']+.1
    project['source_speech']['items']=[dict(cue=i,start=c['start'],end=c['end'],text=c['text'],
        role='participant',confidence=.95,priority=.8,evidence='direct exchange',hook_score=0)
        for i,c in enumerate(cues)]
    return project


def _short_ai_draft():
    return {'title':'Confirmed developments','synopsis':'Verified exchanges',
            'last_confirmed_event':'A participant continues speaking.',
            'selections':[{'start':0,'end':2,'part':1,'section':'development',
                           'reason':'Opening exchange','priority':.8,
                           'source_cue_ids':['0'],'keep_original':True}]}


def test_sampled_cues_cannot_falsely_exhaust_full_footage_budget():
    project=_many_clean_cues(120)
    sampled=_planner_catalog(project['reaction_cues'],project['metadata']['duration'],4,8)
    assert sum(c['end']-c['start'] for c in sampled)<216
    blocks=_clean_footage(project)
    hook=dict(start=0,end=0,title='',reason='Hook off',original_audio=False,narration='')
    budget=_effective_duration(project,blocks,hook)
    assert budget['available_seconds']>216
    assert budget['effective_seconds']==240
    project['reaction_duration_budget']=budget
    repaired=_fill_from_full_source(project,_short_ai_draft(),blocks,hook,budget)
    accepted=validate_plan(repaired,project,check_text=False)
    actual=sum(c['end']-c['start'] for c in accepted['selections'])
    assert 216<=actual<=240.05
    assert 1<=sum(bool(c['narration']) for c in accepted['selections'])<=10


def test_true_source_shortfall_lowers_effective_target_without_changing_setting():
    project=_many_clean_cues(25)
    blocks=_clean_footage(project)
    hook=dict(start=0,end=0,title='',reason='Hook off',original_audio=False,narration='')
    budget=_effective_duration(project,blocks,hook)
    project['reaction_duration_budget']=budget
    assert budget['requested_seconds']==240
    assert 50<budget['effective_seconds']<60
    repaired=_fill_from_full_source(project,_short_ai_draft(),blocks,hook,budget)
    accepted=validate_plan(repaired,project,check_text=False)
    assert project['settings']['summary_seconds']==240
    assert sum(c['end']-c['start'] for c in accepted['selections'])>=budget['effective_seconds']*.9


def test_requested_duration_is_not_capped_by_soft_dialogue_ratio():
    project=_many_clean_cues(1000,requested=1800)
    blocks=_clean_footage(project)
    hook=dict(start=0,end=0,title='',reason='Hook off',original_audio=False,narration='')
    budget=_effective_duration(project,blocks,hook)
    assert budget['available_seconds']>1800
    assert budget['effective_seconds']==1800
    assert project['settings']['summary_seconds']==1800


def test_full_source_repair_covers_last_chapter_even_with_high_dialogue_target():
    project=_many_clean_cues(120)
    project['settings']['original_dialogue_ratio']=1.0
    blocks=_clean_footage(project)
    hook=dict(start=0,end=0,title='',reason='Hook off',original_audio=False,narration='')
    budget=_effective_duration(project,blocks,hook)
    project['reaction_duration_budget']=budget
    result=validate_plan(_fill_from_full_source(project,_short_ai_draft(),blocks,hook,budget),
                         project,check_text=False)
    voiced=[x for x in result['selections'] if x['narration']]
    assert 3 <= len(voiced) <= 6
    assert voiced[-1]['start'] > result['selections'][-1]['start']*.65
    assert result['selections'][-1]['end']==pytest.approx(blocks[-1]['end'], abs=.04)
    assert result['outcome']==project['reaction_cues'][-1]['text']


def test_reaction_ratio_is_soft_but_measured():
    from backend.retention import budget,validate
    project,plan=fixture()
    project['settings']['original_dialogue_ratio']=.8
    validate(plan,project)
    measured=budget(plan,project)
    assert measured['requested_ratio']==.8
    assert measured['actual_ratio']<.8


def test_reaction_pipeline_repairs_underfilled_ai_draft_from_full_source(monkeypatch,tmp_path):
    from backend import providers,store
    from backend.reaction_cops import plan_reaction

    project=_many_clean_cues(120)
    project.update(id='reaction-full-catalog',source='fixture.mp4',source_transcript=project['transcript'],
                   summary='',scenes=[],hooks=[],exports=[],preview_exports=[],warnings=[])
    project['settings']['provider']='openai'
    monkeypatch.setattr(store,'project_dir',lambda _id:tmp_path)
    calls=[]

    def fake_ai(prompt,_frames,_settings,_folder,_check,schema):
        calls.append(schema.__name__)
        if schema.__name__=='OptimizedBatch':
            rows=json.loads(prompt.split('\nREQUESTED: ',1)[1])
            return {'cues':[{'source_cue_ids':[c['id']],'text':c['text']} for c in rows],
                    'warnings':[]}
        if schema.__name__=='SpeechRoles':
            rows=json.loads(prompt.split('\nREQUESTED CUES: ',1)[1])
            return {'items':[{'cue':c['cue'],'role':'participant','confidence':.95,
                              'priority':.8,'evidence':'direct exchange','hook_score':0}
                             for c in rows]}
        if schema.__name__=='ReactionFootagePlan':
            return _short_ai_draft()
        if schema.__name__=='ScheduledNarration':
            rows=json.loads(prompt.split('\nREQUESTED SLOTS: ',1)[1].split('\nACCEPTED',1)[0])
            return {'items':[{'id':c['id'],'text':'Cảnh '*c['min_words']} for c in rows]}
        raise AssertionError(schema.__name__)

    monkeypatch.setattr(providers,'ask_ai',fake_ai)
    result=plan_reaction(project,lambda *_:None,lambda:None)
    budget=result['duration_plan']['reaction_budget']
    assert calls.count('ReactionFootagePlan')==1
    assert budget['requested_seconds']==budget['effective_seconds']==240
    assert 216<=budget['actual_seconds']<=240
    assert result['duration_plan']['status']=='ready'
    assert len(result['narrations'])>=1


def test_token_limited_planner_uses_verified_full_source(monkeypatch,tmp_path):
    from backend import providers,store
    from backend.reaction_cops import plan_reaction

    project=_many_clean_cues(25)
    project.update(id='reaction-token-fallback',source='fixture.mp4',
                   source_transcript=project['transcript'],summary='',scenes=[],hooks=[],
                   exports=[],preview_exports=[],warnings=[])
    project['settings']['provider']='openai'
    monkeypatch.setattr(store,'project_dir',lambda _id:tmp_path)
    calls=[]

    def fake_ai(prompt,_frames,_settings,_folder,_check,schema):
        calls.append(schema.__name__)
        if schema.__name__=='OptimizedBatch':
            rows=json.loads(prompt.split('\nREQUESTED: ',1)[1])
            return {'cues':[{'source_cue_ids':[c['id']],'text':c['text']} for c in rows],
                    'warnings':[]}
        if schema.__name__=='SpeechRoles':
            rows=json.loads(prompt.split('\nREQUESTED CUES: ',1)[1])
            return {'items':[{'cue':c['cue'],'role':'participant','confidence':.95,
                              'priority':.8,'evidence':'direct exchange','hook_score':0}
                             for c in rows]}
        if schema.__name__=='ReactionFootagePlan':
            raise providers.OpenAIRequestTooLarge(10000,11977)
        if schema.__name__=='ScheduledNarration':
            rows=json.loads(prompt.split('\nREQUESTED SLOTS: ',1)[1].split('\nACCEPTED',1)[0])
            return {'items':[{'id':c['id'],'text':'Cảnh '*c['min_words']} for c in rows]}
        raise AssertionError(schema.__name__)

    monkeypatch.setattr(providers,'ask_ai',fake_ai)
    result=plan_reaction(project,lambda *_:None,lambda:None)
    assert calls.count('ReactionFootagePlan')==4
    assert result['duration_plan']['reaction_budget']['effective_seconds']<240
    assert result['duration_plan']['status']=='ready'
    assert any('tự điều chỉnh' in warning for warning in result['warnings'])
