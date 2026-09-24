import copy
import json
import pytest
from backend import providers, store, preferences
from backend.models import Settings
from backend.source_speech import SpeechRoles
from backend.story_schedule import ScheduledNarration
from backend.plan_first import FootagePlan, plan_first, contract_check
from backend.story import plan_story


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'DB', tmp_path/'test.sqlite3')
    store.init()
    p=store.create('Plan first test', {'kind':'upload','file':'source.mp4'})
    p.update(metadata={'duration':100,'has_audio':False},summary='A complete evidenced story.',scenes=[])
    p['settings'].update(summary_seconds=45,language='English')
    rows=[(0,10,'opening',1),(10,28,'development',1),(50,60,'ending',1)]
    footage={'title':'Story','synopsis':'Story','outcome':'Known','lesson':'Care',
             'hook':{'start':80,'end':84,'title':'Hook','reason':'Evidence'},
             'selections':[dict(start=a,end=b,part=part,section=section,reason='Source',priority=.9,
                                evidence='Source statement',keep_original=False) for a,b,section,part in rows]}
    calls=[]
    def ask(prompt,*args):
        model=args[-1];calls.append(model)
        from backend.story_bridges import CoverageReview
        if model is CoverageReview:
            return dict(complete=True,issues=[],repairs=[])
        if model is SpeechRoles:
            cues=json.JSONDecoder().raw_decode(prompt.split('REQUESTED CUES: ',1)[1])[0]
            return {'items':[dict(cue=c['cue'],role='participant',confidence=.95,priority=.9,evidence='Actual participant exchange') for c in cues]}
        if model is FootagePlan:
            assert 'narration' not in model.model_json_schema()['$defs']['Footage']['properties']
            return copy.deepcopy(footage)
        assert model is ScheduledNarration
        assert store.read(p['id'])['duration_plan']['status']=='planned'
        slots=json.JSONDecoder().raw_decode(prompt.split('REQUESTED SLOTS: ',1)[1])[0]
        return {'items':[{'id':s['id'],'text':' '.join(['word']*s['target_words'])} for s in slots]}
    monkeypatch.setattr(providers,'ask_ai',ask)
    return p,footage,calls,ask


def test_locks_and_persists_footage_before_writing(environment):
    p,footage,calls,_=environment
    out=plan_story(p,lambda *a:None,lambda:None)
    assert out['duration_plan']['status']=='ready'
    assert out['duration_plan']['minimum']==40.5
    assert calls[0] is FootagePlan and ScheduledNarration in calls
    assert out['story_plan']['selections'][0]['start']==0
    contract_check(out)


def test_resume_after_prose_failure_reuses_locked_plan(environment,monkeypatch):
    p,_,calls,ask=environment
    def fail(prompt,*args):
        if args[-1] is ScheduledNarration:
            raise RuntimeError('network unavailable')
        return ask(prompt,*args)
    monkeypatch.setattr(providers,'ask_ai',fail)
    with pytest.raises(RuntimeError,match='network'):
        plan_first(p,lambda *a:None,lambda:None)
    saved=store.read(p['id'])
    assert saved['duration_plan']['status']=='planned' and not saved.get('story_plan')
    monkeypatch.setattr(providers,'ask_ai',ask)
    result=plan_first(saved,lambda *a:None,lambda:None)
    assert calls.count(FootagePlan)==1
    assert result['duration_plan']['status']=='ready'


def test_invalid_budget_never_starts_prose(environment):
    p,footage,calls,_=environment
    footage['selections'][1]['end']=11
    with pytest.raises(ValueError,match='ngân sách'):
        plan_first(p,lambda *a:None,lambda:None)
    assert calls==[FootagePlan]*3
    assert not store.read(p['id']).get('duration_plan')


def test_contract_blocks_missing_plan_or_changed_slots(environment):
    p,_,_,_=environment
    with pytest.raises(ValueError,match='chưa sẵn sàng'):
        contract_check(p)
    out=plan_first(p,lambda *a:None,lambda:None)
    out['narrations'][0]['target_duration']+=1
    with pytest.raises(ValueError,match='không khớp slot'):
        contract_check(out)


def test_multipart_locks_every_part_and_counts_hook_once(environment):
    p,footage,_,_=environment
    p['settings'].update(output_mode='parts',part_count=2,part_seconds=30)
    footage['selections']=[dict(start=a,end=b,part=part,section=section,reason='Source',priority=.9,
        evidence='Source statement',keep_original=False) for a,b,section,part in
        [(0,10,'opening',1),(10,24,'development',1),(40,54,'development',2),(60,74,'ending',2)]]
    out=plan_first(p,lambda *a:None,lambda:None)
    assert {s['part'] for s in out['duration_plan']['slots']}=={1,2}
    from backend.story import duration_budget_stats
    assert duration_budget_stats(out['story_plan'],out)['totals']=={1:28,2:28}
    contract_check(out)


def test_selected_original_dialogue_survives_schedule(environment):
    p,footage,_,_=environment
    p['metadata']['has_audio']=True
    p['transcript']=[dict(id='c0',start=10,end=13,text='An important exchange.')]
    footage['selections']=[dict(start=a,end=b,part=1,section=section,reason='Source',priority=.9,
        evidence='Source statement',keep_original=original) for a,b,section,original in
        [(0,10,'opening',False),(10,13,'development',True),(13,31,'development',False),(50,57,'ending',False)]]
    out=plan_first(p,lambda *a:None,lambda:None)
    original=[x for x in out['story_plan']['selections'] if not x['narration']]
    assert [(x['start'],x['end']) for x in original]==[(10,13)]


def test_shared_preferences_do_not_switch_existing_workflow(environment):
    p,_,_,_=environment
    old=store.create('Old project',{'kind':'upload','file':'source.mp4'})
    old['settings'].update(production_workflow='legacy',duration_min_ratio=.75)
    old=store.save(old)
    preferences.save_project(p)
    applied,_=preferences.apply(old)
    assert applied['settings']['production_workflow']=='legacy'
    assert applied['settings']['duration_min_ratio']==.75


def test_new_script_does_not_publish_source_second_citations(environment,monkeypatch):
    p,_,_,ask=environment
    def cited(prompt,*args):
        reply=ask(prompt,*args)
        if args[-1] is ScheduledNarration:
            for line in reply['items']:line['text']='At 138 seconds, '+line['text']
        return reply
    monkeypatch.setattr(providers,'ask_ai',cited)
    result=plan_first(p,lambda *a:None,lambda:None)
    assert all('At 138 seconds' not in n['text'] for n in result['narrations'])
    contract_check(result)


def test_opening_and_ending_audio_roles_are_enforced_before_prose(environment):
    p,footage,_,_=environment
    footage['selections'][0]['keep_original']=True
    footage['selections'][-1]['keep_original']=True
    out=plan_first(p,lambda *a:None,lambda:None)
    assert out['story_plan']['selections'][0]['narration'].strip()
    assert out['story_plan']['selections'][-1]['narration'].strip()
    contract_check(out)


def test_all_original_draft_is_rebalanced_inside_same_footage(environment):
    p,footage,_,_=environment
    p['metadata']['has_audio']=True
    p['transcript']=[dict(id='c0',start=14,end=17,text='Important decisive statement.')]
    for x in footage['selections']:x['keep_original']=True
    out=plan_first(p,lambda *a:None,lambda:None)
    rows=out['story_plan']['selections']
    assert [(x['start'],x['end']) for x in rows if not x['narration']]==[(14,17)]
    assert sum(x['end']-x['start'] for x in rows)==38
    assert rows[0]['narration'] and rows[-1]['narration']
    contract_check(out)


def test_unavailable_dialogue_is_not_replaced_with_silence(environment):
    p,footage,_,_=environment
    p['metadata']['has_audio']=True
    for x in footage['selections']:x['keep_original']=True
    out=plan_first(p,lambda *a:None,lambda:None)
    assert all(x['narration'] for x in out['story_plan']['selections'])
    assert out['story_plan']['hook']['original_audio'] is False
    contract_check(out)


def test_oversized_draft_reaches_prose_without_undercutting_minimum(environment):
    p,footage,calls,_=environment
    p['settings']['summary_seconds']=45
    footage['selections'][1]['end']=27
    extra=copy.deepcopy(footage['selections'][1]);extra.update(start=28,end=52,priority=.1)
    footage['selections'].insert(2,extra)
    footage['selections'][-1].update(start=60,end=70)
    result=plan_first(p,lambda *a:None,lambda:None)
    assert calls.count(FootagePlan)==1
    from backend.story import duration_budget_stats
    assert 40.5<=duration_budget_stats(result['story_plan'],result)['totals'][1]<=45
    contract_check(result)


def test_commentary_only_source_gets_new_narration_without_original_audio(environment,monkeypatch):
    p,_,_,ask=environment
    p['metadata']['has_audio']=True
    p['transcript']=[dict(id='c0',start=14,end=17,text='Original source AI commentary')]
    def classify_commentary(prompt,*args):
        response=ask(prompt,*args)
        if args[-1] is SpeechRoles:
            for item in response['items']:item['role']='commentary'
        return response
    monkeypatch.setattr(providers,'ask_ai',classify_commentary)
    out=plan_first(p,lambda *a:None,lambda:None)
    assert all(x['narration'] for x in out['story_plan']['selections'])
    assert not out['story_plan']['hook']['original_audio']
    assert out['narrations'] and all('Original source AI commentary' not in n['text'] for n in out['narrations'])
    assert out['source_speech']['items'][0]['role']=='commentary'
    assert 'source_speech' not in p  # Preparing a candidate never mutates the prior edit.
    contract_check(out)
