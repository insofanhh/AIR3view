import json
import copy
import pytest
from backend import source_speech as speech
from backend.speech_recovery import partition
from backend.media import Cancelled
from test_source_speech import sample


def role(cue,kind='participant'):
    return dict(cue=cue,role=kind,confidence=.9,priority=.8,evidence='Role from source context',hook_score=0)


def requested(prompt):
    return json.JSONDecoder().raw_decode(prompt.split('REQUESTED CUES: ',1)[1])[0]


def test_only_missing_or_duplicate_ids_retried_and_accepted_immutable(tmp_path):
    p,_=sample();calls=[]
    def ask(prompt,*a):
        ids=[r['cue'] for r in requested(prompt)];calls.append(ids)
        if len(calls)==1:return {'items':[role(0),role(1),role(1,'commentary'),role(3),role(4),role(99)]}
        assert ids==[1,2]
        return {'items':[role(1,'commentary'),role(2),role(0,'commentary')]}
    out=speech.classify(p,ask,tmp_path,lambda *a:None,lambda:None)
    assert calls==[[0,1,2,3,4],[1,2]]
    assert out['items'][0]['role']=='participant'
    assert out['items'][1]['role']=='commentary'
    assert len(out['items'])==5


def test_identical_duplicates_are_not_silently_chosen(tmp_path):
    p,_=sample();calls=[]
    def ask(prompt,*a):
        ids=[r['cue'] for r in requested(prompt)];calls.append(ids)
        if len(calls)==1:return {'items':[role(i) for i in ids]+[role(2)]}
        return {'items':[role(i) for i in ids]}
    speech.classify(p,ask,tmp_path,lambda *a:None,lambda:None)
    assert calls[1]==[2]


def test_failed_retry_advances_generation_and_reuses_valid_ids(tmp_path):
    p,_=sample();prompts=[]
    def missing(prompt,*a):
        prompts.append(prompt)
        return {'items':[role(i) for i in [0,1,2,3] if i in {r['cue'] for r in requested(prompt)}]}
    with pytest.raises(ValueError,match='4/5'):
        speech.classify(p,missing,tmp_path,lambda *a:None,lambda:None)
    first=set(prompts);next_prompts=[]
    def fix(prompt,*a):
        next_prompts.append(prompt)
        assert [c['cue'] for c in requested(prompt)]==[4]
        return {'items':[role(4)]}
    out=speech.classify(p,fix,tmp_path,lambda *a:None,lambda:None)
    assert len(out['items'])==5 and len(next_prompts)==1
    assert next_prompts[0] not in first
    assert 'GENERATION 5' in next_prompts[0]


def test_partial_work_saved_before_transport_error(tmp_path):
    p,_=sample();calls=[]
    def fail(prompt,*a):
        calls.append(1)
        if len(calls)==1:return {'items':[role(0),role(1),role(2),role(3)]}
        raise RuntimeError('API unavailable')
    with pytest.raises(RuntimeError,match='API unavailable'):
        speech.classify(p,fail,tmp_path,lambda *a:None,lambda:None)
    state=json.loads(next((tmp_path/'source-speech-cache/recovery').glob('*.json')).read_text('utf-8'))
    assert len(state['accepted'])==4 and state['generation']==2


def test_large_batch_recovery_splits_only_remaining_ids(tmp_path):
    p,_=sample();p['transcript']=[dict(id=str(i),start=i,end=i+1,text='Sentence') for i in range(80)]
    calls=[]
    def ask(prompt,*a):
        ids=[r['cue'] for r in requested(prompt)];calls.append(ids)
        return {'items':[] if len(calls)==1 else [role(i) for i in ids]}
    out=speech.classify(p,ask,tmp_path,lambda *a:None,lambda:None)
    assert [len(c) for c in calls]==[80,40,40] and len(out['items'])==80


def test_schema_error_is_repaired_but_value_errors_are_not_hidden(tmp_path):
    p,_=sample();calls=[]
    def ask(prompt,*a):
        calls.append(1)
        if len(calls)==1:return {'items':[{'cue':0,'role':'invalid-role'}]}
        return {'items':[role(r['cue']) for r in requested(prompt)]}
    assert len(speech.classify(p,ask,tmp_path,lambda *a:None,lambda:None)['items'])==5
    assert len(calls)==2


def test_corrupt_complete_cache_does_not_become_unknown(tmp_path):
    p,_=sample();calls=[]
    def ask(prompt,*a):
        calls.append(1)
        return {'items':[role(r['cue']) for r in requested(prompt)]}
    first=speech.classify(p,ask,tmp_path,lambda *a:None,lambda:None)
    path=next((tmp_path/'source-speech-cache').glob('*.json'));path.write_text('{broken','utf-8')
    second=speech.classify(p,ask,tmp_path,lambda *a:None,lambda:None)
    assert first==second and len(calls)==1  # Recovery checkpoint restores the valid map.


def test_cancelled_repair_never_publishes_partial_complete_batch(tmp_path):
    p,_=sample();calls=[]
    def ask(prompt,*a):
        calls.append(1)
        if len(calls)==1:return {'items':[role(0)]}
        raise Cancelled('stop')
    with pytest.raises(Cancelled):speech.classify(p,ask,tmp_path,lambda *a:None,lambda:None)
    assert not list((tmp_path/'source-speech-cache').glob('*.json'))
    assert len(calls)==2
