import copy
import re
from pathlib import Path
import pytest
from backend import providers, store
from backend.voice_repair import adaptive_budget, source_evidence, DurationMismatchError
from backend.voice_repair import repair_text


def test_budget_interpolates_between_short_and_long_measurements():
    text=' '.join(['word']*51)
    history=[{'units':60,'measured':20.575},{'units':51,'measured':15.92}]
    desired,lo,hi=adaptive_budget(text,15.92,18.26,history)
    assert 51 < desired < 60
    assert lo > 51 and hi-lo <= 6
    desired,lo,hi=adaptive_budget(' '.join(['word']*60),20.575,18.26,history)
    assert hi < 60 and lo < desired < hi


def test_budget_always_corrects_in_measured_direction():
    assert adaptive_budget('one two three',1,2,[])[1]>3
    assert adaptive_budget('one two three four',3,2,[])[2]<4


def test_word_rounding_does_not_reject_a_candidate_in_fixed_pace_range():
    # Real miss: 52 units took 16.50s for an 18.26s slot. A 55-unit reply is
    # just below preferred 56..60, but predicts 17.45s, within the same +/-5%.
    current=' '.join(['old']*52)
    candidate=' '.join(['new']*55)
    calls=[]
    def ask(*args):
        calls.append(1)
        return {'items':[{'id':'narration','text':candidate}]}
    result=repair_text(current,'Evidence','English',18.26,16.5,
                       {'production_workflow':'plan_first'},None,lambda:None,ask,
                       history=[{'units':52,'measured':16.5}])
    assert result==candidate and len(calls)==1


def test_retry_keeps_one_schema_and_rejects_wrong_direction():
    current=' '.join(['original']*52)
    calls=[]
    def ask(prompt,*args):
        model=args[-1];calls.append(model)
        if len(calls)==1:
            return {'items':[{'id':'narration','text':' '.join(['shorter']*45)}]}
        assert list(model.model_fields)==['text']
        return {'text':' '.join(['expanded']*6)}
    result=repair_text(current,'Evidence','English',18.26,16.5,
                       {'production_workflow':'plan_first'},None,lambda:None,ask)
    assert len(calls)==2 and len(result.split())==58
    assert calls[0] is calls[1]


@pytest.mark.parametrize('candidate',[
    ' '.join(['original']*52),
    'Những người này đã không được các nhân viên cho phép vào trong khu vực vì họ không có giấy tờ.',
    ' '.join(['shorter']*45),
])
def test_alternate_schema_reply_still_checks_language_change_and_direction(candidate):
    def ask(*args):
        return {'items':[{'id':'narration','text':candidate}]}
    with pytest.raises(ValueError):
        repair_text(' '.join(['original']*52),'Evidence','English',18.26,16.5,
                    {'production_workflow':'plan_first'},None,lambda:None,ask)


def test_evidence_resolves_referenced_transcript_without_unrelated_cues():
    p={'source_transcript':[{'id':'c20','start':100,'end':101,'text':'Relevant.'},
                            {'id':'c99','start':900,'end':901,'text':'Unrelated.'}]}
    text=source_evidence(p,{'evidence':'Transcript segments c20 to c56','start':110,'target_duration':10})
    assert 'Relevant.' in text and 'Unrelated.' not in text


def test_loop_passes_three_repairs_without_changing_voice(tmp_path,monkeypatch):
    import gradio_client
    monkeypatch.setattr(store,'DATA',tmp_path)
    monkeypatch.setattr(store,'DB',tmp_path/'studio.sqlite3')
    store.init()
    p=store.create('Test',{'kind':'upload','file':'source.mp4'})
    p['settings'].update(output_mode=None,tts_provider='omnivoice',voice_mode='clone',
                         voice_reference='ref.wav',voice_reference_text='Reference.',voice_speed=1.2,language='English')
    store.asset(p['id'],'ref.wav').write_bytes(b'ref')
    p['narrations']=[dict(id='done',text='Already done.',start=0,enabled=True,evidence='E',
                         audio='',audio_hash='',duration=4,cues=[],caption_version=4,target_duration=4),
                     dict(id='fix',text='Some initial spoken words here.',start=8,enabled=True,evidence='Source statement',
                         audio='',audio_hash='',duration=0,cues=[],caption_version=0,target_duration=4)]
    first=p['narrations'][0];first['audio_hash']=providers.voice_hash(first,p['settings'])
    first['audio']='voices/'+first['audio_hash']+'.wav'
    folder=store.project_dir(p['id']);(folder/'voices').mkdir()
    (folder/first['audio']).write_bytes(b'completed')
    before=copy.deepcopy(first);settings=copy.deepcopy(p['settings'])
    monkeypatch.setattr('backend.reference_voice.ensure_transcript',lambda *a:None)
    monkeypatch.setattr(gradio_client,'Client',lambda *a,**kw:object())
    monkeypatch.setattr(gradio_client,'handle_file',lambda p:p)
    calls=[];aligned=[]
    def generate(client,params,endpoint,destination,*args,**kwargs):
        calls.append(copy.deepcopy(params))
        if len(calls)<=4:
            raise DurationMismatchError('OmniVoice',8 if len(calls)%2 else 2,4)
        destination.write_bytes(b'fixed')
        return {'final_duration':4,'tempo':1}
    monkeypatch.setattr(providers,'generate_voice_audio',generate)
    monkeypatch.setattr(providers,'probe',lambda *a:{'duration':4})
    def ai(prompt,*a):
        target=int(re.search(r'Aim for about (\d+)',prompt)[1])
        return {'items':[{'id':'narration','text':' '.join(['updated']*target)}]}
    monkeypatch.setattr(providers,'ask_ai',ai)
    monkeypatch.setattr(providers,'transcribe',lambda *a,**kw:aligned.append(kw['expected_text']) or [])
    result=providers.synthesize(p,lambda *a:None,lambda:None)
    assert len(calls)==5 and len(aligned)==1
    assert all({k:v for k,v in call.items() if k!='text'}=={k:v for k,v in calls[0].items() if k!='text'} for call in calls)
    assert result['settings']==settings and result['narrations'][0]==before
    state=result['voice_repair_state']['fix']
    assert len(state['history'])==4 and state['status']=='fitted' and state['generation']==4
