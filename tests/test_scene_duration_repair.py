import copy
import pytest
from backend import store
from backend.models import Settings
from backend.story import duration_plan_manifest, plan_fingerprint
from backend.scene_duration_repair import adjust
from backend.plan_first import geometry


def project(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DATA',tmp_path);monkeypatch.setattr(store,'DB',tmp_path/'test.sqlite3');store.init()
    p=store.create('Scene fit',{'kind':'upload','file':'source.mp4'})
    p.update(metadata={'duration':100,'has_audio':False},scenes=[],summary='A story.',transcript=[],source_transcript=[])
    p['settings'].update(output_mode='single',summary_seconds=45,duration_min_ratio=.75,narration_style='storytelling',production_workflow='plan_first',language='English')
    p['story_plan']={'title':'Story','synopsis':'Story','outcome':'Known outcome','lesson':'Evidence',
        'hook':{'start':80,'end':84,'title':'Hook','reason':'Evidence','original_audio':False,'narration':'Hook context.'},
        'selections':[
            {'id':'sel0','start':0,'end':10,'part':1,'section':'opening','reason':'Context','priority':.9,'evidence':'Context','narration':'Short context.','narration_offset':0},
            {'id':'sel1','start':10,'end':28,'part':1,'section':'development','reason':'Action','priority':.9,'evidence':'Action','narration':'Action.','narration_offset':0},
            {'id':'sel2','start':50,'end':60,'part':1,'section':'ending','reason':'Outcome','priority':.9,'evidence':'Outcome','narration':'Outcome.','narration_offset':0}]}
    p['narrations']=[dict(id='story-sel0',segment_id='sel0',start=0,text='Short context.',section='opening',part=1,evidence='Context',enabled=True,target_duration=9.96,audio='',audio_hash='',duration=0,cues=[],caption_version=0)]
    p['plan_fingerprint']=plan_fingerprint(p);p['duration_plan']={**duration_plan_manifest(p['story_plan'],p),'status':'ready','source_policy_version':2,'retention_policy_version':1,'story_bridge_version':1,'hook_policy_version':1,'input_fingerprint':p['plan_fingerprint'],'schedule':copy.deepcopy(p['story_plan']),'geometry':geometry(p['story_plan'])}
    return p


def test_short_audio_shrinks_video_slot_and_invalidates_only_that_voice(tmp_path,monkeypatch):
    p=project(tmp_path,monkeypatch)
    result=adjust(p,p['narrations'][0],5.2,9.96)
    assert result is not None
    row=next(x for x in result['story_plan']['selections'] if x['id']=='sel0')
    narration=result['narrations'][0]
    assert 5.18<=narration['target_duration']<=5.23
    assert row['end']<10 and narration['audio']=='' and narration['duration']==0
    assert len(result['voice_repair_state']['story-sel0']['scene_adjustments'])==1


def test_adjustment_is_bounded_and_does_not_touch_completed_neighbors(tmp_path,monkeypatch):
    p=project(tmp_path,monkeypatch);p['narrations'].append(dict(id='story-sel1',segment_id='sel1',start=10,text='Action.',section='development',part=1,evidence='Action',enabled=True,target_duration=17.96,audio='voices/done.wav',audio_hash='done',duration=17.9,cues=[],caption_version=4))
    result=adjust(p,p['narrations'][0],5.2,9.96)
    assert result is not None
    assert result['narrations'][1]==p['narrations'][1]
    p['voice_repair_state']={'story-sel0':{'scene_adjustments':[{}, {}, {}]}}
    assert adjust(p,p['narrations'][0],5.2,9.96) is None


def test_cannot_shrink_below_minimum_video_or_relax_original_ratio(tmp_path,monkeypatch):
    p=project(tmp_path,monkeypatch);p['settings']['duration_min_ratio']=.9
    from backend.story import plan_fingerprint
    p['plan_fingerprint']=plan_fingerprint(p);p['duration_plan']['input_fingerprint']=p['plan_fingerprint']
    before=copy.deepcopy(p)
    assert adjust(p,p['narrations'][0],5.2,9.96) is None
    assert p==before


def test_grow_by_borrowing_unfinished_neighbor_keeps_total_and_ids(tmp_path,monkeypatch):
    p=project(tmp_path,monkeypatch)
    p['story_plan']['selections'][1]['evidence']='Context'
    p['duration_plan']['schedule']=copy.deepcopy(p['story_plan'])
    n=copy.deepcopy(p['narrations'][0]);n.update(id='story-sel1',segment_id='sel1',start=10,text='Next context.',target_duration=17.96)
    p['narrations'].append(n)
    before=sum(r['end']-r['start'] for r in p['story_plan']['selections'])
    candidate=adjust(p,p['narrations'][0],12,9.96)
    assert candidate is not None
    assert candidate['voice_repair_state']['story-sel0']['scene_adjustments'][0]['strategy']=='borrow_next'
    assert sum(r['end']-r['start'] for r in candidate['story_plan']['selections'])==pytest.approx(before)
    assert [r['id'] for r in candidate['story_plan']['selections']]==['sel0','sel1','sel2']
    from backend.plan_first import contract_check
    contract_check(candidate)
    from backend.scene_duration_repair import apply_in_place
    pending=p['narrations'][:]
    apply_in_place(p,candidate)
    assert pending[0] is p['narrations'][0] and pending[1] is p['narrations'][1]
    assert pending[1]['start']>10 and pending[1]['target_duration']<17.96


def test_narration_perspective_is_restored_only_for_recorded_model_regression():
    from backend.voice_repair import restore_perspective,perspective_drift,repair_text
    baseline='An intoxicated driver causes a crash and officers investigate the scene.'
    quote='What are you doing to me? Put your hands behind your back.'
    n=dict(id='story-sel0',segment_id='sel0',enabled=True,audio='',text=quote)
    p=dict(narrations=[n],voice_repair_state={'story-sel0':dict(history=[dict(text=baseline),dict(text=quote)])},
           story_plan={'selections':[dict(id='sel0',narration=quote)]},duration_plan={'schedule':{'selections':[dict(id='sel0',narration=quote)]}})
    assert restore_perspective(p)
    assert n['text']==baseline and p['story_plan']['selections'][0]['narration']==baseline
    n['text']='My manual edit is intentional.'
    assert not restore_perspective(p)
    assert perspective_drift(baseline,quote)
    with pytest.raises(ValueError,match='người dẫn chuyện'):
        repair_text(baseline,'Evidence','English',4,8,{},None,lambda:None,lambda *a:{'text':quote},max_attempts=1)


def test_budget_does_not_interpolate_between_prose_and_fast_quote():
    from backend.voice_repair import adaptive_budget
    text='What are you doing to me, man? Put your hands on your back now.'
    desired,low,high=adaptive_budget(text,2.88,5.427,[dict(units=19,measured=7.52),dict(units=14,measured=2.88)])
    assert desired>=25 and low>=24


def test_synthesis_reuses_measured_audio_after_adjusting_scene(tmp_path,monkeypatch):
    import wave,struct
    import gradio_client
    from backend import providers
    from backend.voice_repair import DurationMismatchError
    p=project(tmp_path,monkeypatch)
    raw=tmp_path/'raw.wav'
    with wave.open(str(raw),'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(48000)
        wav.writeframes(b'\x00\x00'*round(5.2*48000))
    p['settings'].update(tts_provider='vieneu',voice_mode='design',vieneu_voice='Test')
    monkeypatch.setattr(gradio_client,'Client',lambda *a,**kw:object())
    monkeypatch.setattr('backend.vieneu.ensure_ready',lambda *a:None)
    monkeypatch.setattr('backend.vieneu.parameters',lambda *a,**kw:{})
    calls=[]
    def generate(*args,**kwargs):
        calls.append(1)
        raise DurationMismatchError('VieNeu',5.2,kwargs['target_duration'],audio_path=raw)
    monkeypatch.setattr(providers,'generate_voice_audio',generate)
    def fit(audio,destination,check,target,*args):
        assert '.wav' in str(audio) and 5.18<target<5.23
        destination.parent.mkdir(exist_ok=True);destination.write_bytes(raw.read_bytes())
        return {'final_duration':5.2,'tempo':1}
    monkeypatch.setattr(providers,'fit_voice_audio',fit)
    monkeypatch.setattr(providers,'transcribe',lambda *a,**kw:[])
    monkeypatch.setattr(providers,'ask_ai',lambda *a:pytest.fail('No text rewrite required'))
    result=providers.synthesize(p,lambda *a:None,lambda:None)
    assert len(calls)==1
    assert result['narrations'][0]['duration']==pytest.approx(5.2)
    assert result['narrations'][0]['caption_version']==4
