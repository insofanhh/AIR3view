import copy
import pytest
from backend import store, providers
from backend.models import Narration
from backend.narration_groups import join_short_slots, repair_existing
from backend.story import validate_plan, plan_fingerprint, plan_is_current


def slot(a, b, text='Some narration.', part=1, section='development'):
    return dict(start=a,end=b,narration=text,part=part,section=section,
                narration_offset=0,evidence='Source evidence',reason='Story',priority=.9)


def test_joins_short_adjacent_voice_without_crossing_dialogue_or_gaps():
    slots=[slot(0,10,section='opening'),slot(10,11.2),slot(11.2,17,''),
           slot(17,18.1),slot(18.1,20),slot(22,23),slot(23,28,part=2,section='ending')]
    groups=join_short_slots(slots)
    assert [indices for _,indices in groups]==[[0,1],[2],[3,4],[5],[6]]
    assert sum(s['end']-s['start'] for s,_ in groups)==pytest.approx(26)
    assert groups[1][0]['narration']==''
    assert groups[0][0]['section']=='opening'


def test_repair_preserves_cache_on_untouched_segments(tmp_path, monkeypatch):
    monkeypatch.setattr(store,'DATA',tmp_path)
    monkeypatch.setattr(store,'DB',tmp_path/'studio.sqlite3')
    store.init()
    p=store.create('Test',{'kind':'upload','file':'source.mp4'})
    p['metadata']={'duration':60,'has_audio':False}
    p['settings'].update(summary_seconds=45,tts_provider='vieneu',production_workflow='legacy',duration_min_ratio=.75)
    raw=dict(title='Story',synopsis='Story',outcome='Known',lesson='Care',
             hook=dict(start=50,end=54,title='Hook',reason='Evidence'),
             selections=[slot(0,8,section='opening'),slot(8,9.2),slot(9.2,20),slot(20,35,section='ending')])
    p['story_plan']=validate_plan(raw,p,check_text=False)
    p['plan_fingerprint']=plan_fingerprint(p)
    p['narrations']=[]
    for i,s in enumerate(p['story_plan']['selections']):
        n=Narration(id='n'+str(i),segment_id=s['id'],text=s['narration'],start=s['start'],
                    section=s['section'],target_duration=round(s['end']-s['start']-.04,3)).model_dump()
        if i!=1:
            n.update(audio=f'cached{i}.wav',duration=n['target_duration'],caption_version=4)
            n['audio_hash']=providers.voice_hash(n,p['settings'])
            store.asset(p['id'],n['audio']).write_bytes(b'cached')
        p['narrations'].append(n)
    before=copy.deepcopy(p)
    out=repair_existing(p,lambda *a:None,lambda:None)
    assert len(out['narrations'])==3 and plan_is_current(out)
    assert out['narrations'][0]['target_duration']==9.16
    assert out['narrations'][0]['audio_hash']==''
    for n,old in zip(out['narrations'][1:],before['narrations'][2:]):
        assert n['audio_hash']==old['audio_hash'] and n['audio']==old['audio']
        assert n['audio_hash']==providers.voice_hash(n,out['settings'])
    assert p==before
    assert repair_existing(out,lambda *a:None,lambda:None)==out
