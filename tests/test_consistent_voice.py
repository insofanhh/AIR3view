import copy
from pathlib import Path
import pytest
from backend import providers, media, vieneu
from backend.models import Settings
from backend.voice_repair import DurationMismatchError


@pytest.mark.parametrize('engine,pace,seconds,allowed',[
    ('VieNeu',1.25,12.4,True), ('VieNeu',1.25,10,False),
    ('VieNeu',1,7,False), ('OmniVoice',1,10.4,True), ('OmniVoice',1,12,False),
])
def test_plan_first_fits_only_near_selected_pace(tmp_path,monkeypatch,engine,pace,seconds,allowed):
    raw=tmp_path/'raw.wav';out=tmp_path/'result.wav';filters=[]
    monkeypatch.setattr(providers,'_request_voice_audio',lambda *a:raw)
    monkeypatch.setattr(providers,'probe',lambda p:{'duration':seconds if p==raw else 10})
    def ffmpeg(args,**kwargs):
        filters.append(args[args.index('-af')+1]);Path(args[-1]).write_bytes(b'audio')
    monkeypatch.setattr(media,'run',ffmpeg)
    def generate():
        providers.generate_voice_audio(None,{},'/wrapper',out,lambda *a:None,lambda:None,
                                       0,'Test',target_duration=10,stable_speed=pace,engine=engine)
    if allowed:
        generate()
        assert filters==[f'atempo={seconds/10:.8f}']
    else:
        with pytest.raises(DurationMismatchError) as error:
            generate()
        assert error.value.measured==pytest.approx(seconds/pace)
        assert not out.exists() and not filters


def test_plan_first_has_separate_cache_from_stretched_legacy_audio():
    n={'text':'A stable voice.'}
    settings=Settings().model_dump()
    legacy=providers.voice_hash(n,settings)
    settings['production_workflow']='plan_first'
    assert providers.voice_hash(n,settings)!=legacy


def test_vieneu_uses_fixed_lower_temperature_in_new_workflow():
    class Client:
        def view_api(self,**kwargs):
            return {'named_endpoints':{'/wrapper':{'parameters':[{'parameter_name':x} for x in
              ['param_0','param_1','param_2','param_3','param_5','param_6','param_7','param_8','param_9','param_10']]}}}
    settings=Settings(vieneu_voice='Preset',production_workflow='plan_first').model_dump()
    assert vieneu.parameters(Client(),settings,'Text')['param_8']==.4
    settings['production_workflow']='legacy'
    assert vieneu.parameters(Client(),settings,'Text')['param_8']==.8
