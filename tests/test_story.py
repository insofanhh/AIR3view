import copy
import shutil
import pytest
from backend import store, providers
from backend.models import Settings, StoryAnswer
from backend.story import validate_plan, plan_story, plan_fingerprint
from backend.timeline import build
from backend.media import run, probe, FFMPEG
from backend.render import render_part, write_subtitles


def project():
    return {'id':'a'*32,'source':{'kind':'upload','file':'source.mp4'},'metadata':{'duration':60,'has_audio':False},
            'settings':Settings(output_mode='single',summary_seconds=40,language='English').model_dump(),
            'narrations':[],'transcript':[],'scenes':[],'summary':'The entire story ends peacefully.',
            'hooks':[],'warnings':[],'revision':0}


def answer():
    return dict(title='A complete story',synopsis='An argument is resolved.',outcome='The people reach an agreement.',lesson='Calm communication helps.',
        hook=dict(start=22,end=25,title='An argument',reason='Raised voices in the dialogue'),
        selections=[dict(start=a,end=b,part=1,section=section,reason=reason,priority=.9,narration=text,narration_offset=offset,evidence=f'At {a}s')
        for a,b,section,reason,text,offset in [
            (0,10,'opening','Context','Two people disagree.',3),
            (20,30,'development','The decisive exchange','They explain their reasons.',1),
            (50,60,'ending','Preserve the resolution','They agree. Stay calm.',1)]])


def planned(monkeypatch,p=None,raw=None):
    p=p or project()
    monkeypatch.setattr(providers,'ask_ai',lambda *args:copy.deepcopy(raw or answer()))
    return plan_story(p,lambda *a:None,lambda:None)


def test_single_video_selects_beginning_middle_ending_and_maps_voice(monkeypatch):
    p=planned(monkeypatch)
    p['transcript']=[{'id':'c1','start':27,'end':28,'text':'Listen','words':[{'text':'Listen','start':27,'end':28}]}]
    p['plan_fingerprint']=plan_fingerprint(p)
    for n in p['narrations']:
        n.update(audio='voice.wav',duration=2,audio_hash=providers.voice_hash(n,p['settings']),cues=[{'id':'c0','start':0,'end':2,'text':n['text']}])
    t=build(p,True)
    assert len(t['parts'])==1 and t['duration']==33
    assert [(c['source_start'],c['source_end']) for c in t['clips']]==[(22,25),(0,10),(20,30),(50,60)]
    assert [v['start'] for v in t['voices']]==[6,14,24]
    assert next(c for c in t['cues'] if c['text']=='Listen')['words'][0]['start']==20
    assert all(c['kind']!='freeze' for c in t['clips'])
    assert [n['section'] for n in p['narrations']]==['opening','development','ending']


def test_exact_requested_part_count_and_no_extra_tail(monkeypatch):
    p=project();p['settings'].update(output_mode='parts',part_count=2,part_seconds=30)
    raw=answer();raw['selections'][0]['end']=12;raw['selections'][-1].update(start=35,part=2)
    p=planned(monkeypatch,p,raw)
    t=build(p)
    assert [x['duration'] for x in t['parts']]==[25,25]
    assert len(t['parts'])==2 and t['parts'][-1]['end']==t['duration']


def test_zero_duration_uses_source_in_prompt_validation_and_timeline(monkeypatch):
    p=project()
    p['settings']=Settings(output_mode='single',summary_seconds=0,language='English').model_dump()
    raw=answer()
    raw['selections'][1].update(start=10,end=40)
    raw['selections'][-1].update(start=43,end=60)
    prompts=[]
    def fake(prompt,*args,**kwargs):
        prompts.append(prompt)
        return copy.deepcopy(raw)
    monkeypatch.setattr(providers,'ask_ai',fake)
    p=plan_story(p,lambda *a:None,lambda:None)
    assert 'each aiming for 60 seconds' in prompts[0]
    assert p['settings']['summary_seconds']==0
    assert build(p)['duration']==60
    raw['selections'][-1]['start']=40
    with pytest.raises(ValueError,match='mục tiêu'):
        validate_plan(raw,p)  # The hook also counts toward the source-length budget.


def test_automatic_duration_does_not_change_explicit_or_part_budgets():
    from backend.story import output_budget
    assert output_budget(Settings(output_mode='single',summary_seconds=0).model_dump(),152.685)==(1,152.685)
    assert output_budget(Settings(output_mode='single',summary_seconds=200).model_dump(),152.685)==(1,200)
    assert output_budget(Settings(output_mode='parts',summary_seconds=0,part_count=2,part_seconds=45).model_dump(),152.685)==(2,45)
    assert output_budget(Settings(output_mode='single',summary_seconds=0).model_dump(),3600)==(1,3600)


@pytest.mark.parametrize('fault',['missing_end','overlap','long_voice','short_opening','budget','missing_part','outside'])
def test_rejects_incomplete_or_impossible_editorial_plan(fault):
    p=project();a=answer()
    if fault=='missing_end': a['selections'][-1]['section']='development'
    if fault=='overlap': a['selections'][1]['start']=5
    if fault=='long_voice': a['selections'][-1]['narration']='long '*70
    if fault=='short_opening': a['selections'][0]['narration_offset']=0
    if fault=='budget': p['settings']['summary_seconds']=30
    if fault=='missing_part': p['settings'].update(output_mode='parts',part_count=2,part_seconds=40)
    if fault=='outside': a['selections'][-1]['end']=80
    with pytest.raises(ValueError): validate_plan(a,p)


def test_setting_change_blocks_old_plan_but_keeps_source_preview(monkeypatch):
    p=planned(monkeypatch);p['settings']['summary_seconds']=90
    assert build(p)['planned'] is False
    with pytest.raises(ValueError,match='Cấu hình đầu ra'):build(p,True)


@pytest.mark.parametrize('changes,current',[
    ({},True),
    ({'part_count':7,'part_seconds':90},True),
    ({'duck_volume':.28,'title_size':48,'asr_device':'cuda'},True),
    ({'summary_seconds':90},False),
    ({'voice_speed':1.35},False),
    ({'language':'Vietnamese'},False),
])
def test_save_legacy_plan_and_queue_voice_checks_effective_settings(tmp_path,monkeypatch,changes,current):
    from fastapi.testclient import TestClient
    from backend.app import app, QUEUE
    from backend.story import legacy_plan_fingerprint
    monkeypatch.setattr(store,'DATA',tmp_path)
    monkeypatch.setattr(store,'DB',tmp_path/'store.sqlite3')
    monkeypatch.setattr(QUEUE,'put',lambda job:None)
    store.init()
    p=project()
    p['settings']['summary_seconds']=40  # Legacy JSON integer, coerced to float by PUT.
    p=planned(monkeypatch,p)
    p['plan_fingerprint']=legacy_plan_fingerprint(p)
    p=store.save(p)
    body={k:copy.deepcopy(p[k]) for k in ('revision','name','settings','narrations','transcript') if k in p}
    body['name']='Roundtrip plan'
    body['settings'].update(changes)
    client=TestClient(app)
    headers={'X-AIR3view':'studio'}
    url='/api/projects/'+p['id']
    assert client.put(url,json=body,headers=headers).status_code==200
    assert client.get(url+'/timeline').json()['planned'] is current
    response=client.post(url+'/jobs',json={'kind':'voice'},headers=headers)
    assert response.status_code==(200 if current else 422)
    if not current:
        assert 'Cấu hình đầu ra' in response.json()['detail']


def test_fingerprint_ignores_numeric_encoding_and_inactive_story_delay(monkeypatch):
    from backend.story import plan_is_current
    p=planned(monkeypatch)
    p['settings']['summary_seconds']=int(p['settings']['summary_seconds'])
    p['metadata']['duration']=float(p['metadata']['duration'])
    assert plan_is_current(p)
    p['settings']['narration_style']='storytelling'
    p['plan_fingerprint']=plan_fingerprint(p)
    p['settings']['opening_delay']=12
    assert plan_is_current(p)
    p['settings']['original_dialogue_ratio']=.2
    assert not plan_is_current(p)


def test_actual_voice_cannot_overrun_selected_scene(monkeypatch):
    p=planned(monkeypatch)
    for n in p['narrations']:n.update(audio='v.wav',duration=9,audio_hash=providers.voice_hash(n,p['settings']))
    with pytest.raises(ValueError,match='dài hơn cảnh'):build(p,True)


def test_single_output_has_no_part_label(monkeypatch,tmp_path):
    p=planned(monkeypatch);t=build(p)
    write_subtitles(tmp_path,p,t,t['parts'][0])
    events=(tmp_path/'part-001.ass').read_text('utf-8-sig').split('[Events]')[1]
    assert ',Part,' not in events


def test_plan_is_written_after_entire_video_analysis(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DATA',tmp_path);monkeypatch.setattr(store,'DB',tmp_path/'store.sqlite3');store.init()
    p=store.create('Whole story',{'kind':'upload','file':'source.mp4'})
    p.update(metadata={'duration':120,'has_audio':True},frames=[{'time':0,'file':'a.jpg'},{'time':70,'file':'b.jpg'}],
             transcript=[{'id':'end','start':115,'end':118,'text':'The final outcome.'}])
    p['settings'].update(review_enabled=False,summary_seconds=40,narration_style='highlights')
    calls=[]
    def fake(prompt,images,settings,folder,check,response_model=None):
        calls.append(prompt)
        if response_model is StoryAnswer:
            assert len(calls)==3 and 'The final outcome.' in prompt and 'Scene 2' in prompt
            result=answer();result['selections'][-1].update(start=110,end=120)
            return result
        index=len(calls)
        return dict(scenes=[dict(start=(index-1)*60,end=index*60,description=f'Scene {index}',characters=[],evidence='Frames and dialogue',confidence=.9)],narrations=[],hooks=[],summary='Complete through '+str(index*60),requires_insert=False)
    monkeypatch.setattr(providers,'ask_ai',fake)
    result=providers.analyze(p,lambda *a:None,lambda:None)
    assert result['story_plan']['selections'][-1]['end']==120
    assert result['narrations'][-1]['section']=='ending'
    assert len(calls)==3


@pytest.mark.skipif(not shutil.which(FFMPEG),reason='FFmpeg required')
def test_real_montage_renders_selected_source_colors_in_order(tmp_path,monkeypatch):
    import av
    monkeypatch.setattr(store,'DATA',tmp_path);monkeypatch.setattr(store,'DB',tmp_path/'store.sqlite3');store.init()
    p=project();folder=store.project_dir(p['id'])
    run([FFMPEG,'-y','-f','lavfi','-i','color=red:s=160x160:r=30:d=20',
         '-f','lavfi','-i','color=green:s=160x160:r=30:d=20','-f','lavfi','-i','color=blue:s=160x160:r=30:d=20',
         '-filter_complex','[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]','-map','[v]','-c:v','libx264',folder/'source.mp4'])
    p['metadata']=probe(folder/'source.mp4');p=planned(monkeypatch,p)
    # Render selected visual sequence; synthesis is covered separately by audio tests.
    p['narrations']=[];t=build(p,True)
    result=render_part(p,t,t['parts'][0],folder,lambda:None,width=360)
    samples={}
    with av.open(str(store.asset(p['id'],result['file']))) as c:
        for f in c.decode(video=0):
            if round(f.time*30) in (30,120,450,900):samples[round(f.time*30)]=f.to_ndarray(format='rgb24')[200:350,100:250].mean(axis=(0,1))
    assert samples[30][1]>90 and samples[30][0]<20
    assert samples[120][0]>200 and samples[120][1]<20
    assert samples[450][1]>90 and samples[450][0]<20
    assert samples[900][2]>200 and samples[900][0]<20
    assert result['duration']==33
