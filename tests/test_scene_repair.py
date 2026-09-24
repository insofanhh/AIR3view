import copy
import pytest
from backend import providers, story, story_schedule
from backend.models import Settings
from backend.scene_repair import issues, repair


def project():
    return {'id':'s'*32,'source':{'file':'source.mp4'},'metadata':{'duration':100,'has_audio':False},
            'settings':Settings(output_mode='single',summary_seconds=80,language='English').model_dump(),
            'scenes':[],'summary':'Complete story.','transcript':[],'hooks':[],'warnings':[]}


def raw():
    return {'title':'Story','synopsis':'Story','outcome':'Known','lesson':'Care',
      'hook':{'start':80,'end':84,'title':'Hook','reason':'Evidence'},
      'selections':[dict(start=0,end=12,part=1,section='opening',reason='Context',priority=.9,narration='Opening context',narration_offset=0,evidence='start'),
                    dict(start=12,end=25,part=1,section='development',reason='Middle',priority=.9,narration='Development',narration_offset=0,evidence='middle'),
                    dict(start=23,end=60,part=1,section='ending',reason='Ending',priority=.9,narration='Ending',narration_offset=0,evidence='end')]}


def test_scene_repair_fixes_overlap_and_keeps_other_slots(monkeypatch,tmp_path):
    p=project(); draft=raw(); before=copy.deepcopy(draft)
    monkeypatch.setattr(providers,'ask_ai',lambda prompt,*args: {'items':[{'id':2,'start':25,'end':60}]})
    out=repair(draft,p,providers.ask_ai,tmp_path,lambda *a:None,lambda:None)
    assert out['selections'][0]==before['selections'][0]
    assert out['selections'][1]==before['selections'][1]
    assert out['selections'][2]['start']==25
    assert out['selections'][2]['end']==before['selections'][2]['end']
    assert out['selections'][2]['narration']==before['selections'][2]['narration']
    assert out['selections'][2]['evidence']==before['selections'][2]['evidence']
    assert not issues(out,100)


def test_scene_repair_reports_unfixable_and_schedule_does_not_hide_geometry():
    p=project(); draft=raw(); draft['selections'][2]['start']=23
    assert issues(draft,100)
    try:
        story_schedule.schedule(draft,p)
    except ValueError as e:
        assert 'Mốc cảnh chưa hợp lệ' in str(e)
    else:
        raise AssertionError('invalid schedule accepted')


def test_only_invalid_range_is_requested_and_cache_resumes(tmp_path):
    p=project();draft=raw()
    draft['selections'][2].update(start=25,end=110)
    prompts=[]
    def invalid(prompt,*args):
        prompts.append(prompt)
        return {'items':[{'id':0,'start':0,'end':5}]}
    with pytest.raises(ValueError,match='cảnh 3'):
        repair(draft,p,invalid,tmp_path,lambda *a:None,lambda:None)
    assert len(prompts)==3
    def valid(prompt,*args):
        assert 'GENERATION: 4' in prompt
        return {'items':[{'id':2,'start':25,'end':60}]}
    result=repair(draft,p,valid,tmp_path,lambda *a:None,lambda:None)
    assert result['selections'][:2]==draft['selections'][:2]
    def no_call(*a):
        raise AssertionError('accepted repair should be reused')
    assert repair(draft,p,no_call,tmp_path,lambda *a:None,lambda:None)==result


def test_cached_overlong_hook_is_normalized_without_changing_clips(tmp_path):
    p=project();draft=raw()
    draft['selections'][2]['start']=25
    draft['hook'].update(start=.24,end=7.44)
    out=repair(draft,p,lambda *a:None,tmp_path,lambda *a:None,lambda:None)
    assert out['hook']['end']-out['hook']['start']==7
    assert out['selections']==draft['selections']


def test_geometry_network_errors_do_not_consume_repair_loop(tmp_path):
    p=project();draft=raw();draft['selections'][2].update(start=25,end=110)
    calls=[]
    def fail(*a):
        calls.append(1)
        raise RuntimeError('network unavailable')
    with pytest.raises(RuntimeError,match='network unavailable'):
        repair(draft,p,fail,tmp_path,lambda *a:None,lambda:None)
    assert len(calls)==1
