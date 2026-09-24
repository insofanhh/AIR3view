import copy
import pytest
from backend.narration_text import clean_narration, prepare_clean_narration
from backend import store


@pytest.mark.parametrize('source,expected',[
    ('At 138 seconds, Martina insists to 911 dispatchers.', 'Martina insists to 911 dispatchers.'),
    ('At 138.5 seconds, she calls. At 145 seconds, officers arrive.', 'She calls. Officers arrive.'),
    ('Ở giây thứ 138, Martina gọi tổng đài.', 'Martina gọi tổng đài.'),
    ('Tại mốc 02:18, cô ấy gọi cảnh sát.', 'Cô ấy gọi cảnh sát.'),
    ('在第138秒，女子打电话。','女子打电话。'),
    ('Officers arrive [02:18–02:24].','Officers arrive.'),
    ('Officers arrive (source 02:18).','Officers arrive.'),
])
def test_removes_editing_citations_only(source,expected):
    assert clean_narration(source)==expected


@pytest.mark.parametrize('text',[
    'At 9:30 PM, she calls the police.',
    'The call was at 02:18 AM on March 21, 2025.',
    'After 138 seconds, they reached the hospital.',
    'He waited 30 seconds and called again.',
    'Cuộc gọi diễn ra lúc 9 giờ 30; hai năm sau người mẹ tìm thấy con.',
])
def test_preserves_real_event_time_and_duration(text):
    assert clean_narration(text)==text


def test_changes_audio_and_captions_together_preserving_evidence(monkeypatch):
    evidence='Source video 138–148s; transcript c8.'
    p={'id':'a'*32,'settings':{'voice_speed':1.2},'exports':[{'file':'old.mp4'}],
       'transcript':[{'text':'At 138 seconds, actual source dialogue.'}],
       'narrations':[dict(id='n1',segment_id='s1',enabled=True,text='At 138 seconds, Martina calls.',
                          audio='old.wav',audio_hash='oldhash',duration=10,cues=[{'text':'At 138 seconds, Martina calls.'}],caption_version=4,
                          evidence=evidence,start=138,target_duration=10),
                     dict(id='n2',enabled=True,text='Police arrive.',audio='keep.wav',cues=[],caption_version=4)],
       'story_plan':{'selections':[{'id':'s1','start':138,'end':148,'evidence':evidence,'narration':'At 138 seconds, Martina calls.'}]}}
    original=copy.deepcopy(p);saved=[]
    monkeypatch.setattr(store,'save',lambda x:saved.append(x) or x)
    out=prepare_clean_narration(p)
    assert out['narrations'][0]['text']=='Martina calls.'
    assert out['narrations'][0]['audio_hash']=='' and out['narrations'][0]['cues']==[]
    assert out['story_plan']['selections'][0]['evidence']==evidence
    assert out['transcript']==original['transcript'] and out['settings']==original['settings']
    assert out['narrations'][1]==original['narrations'][1] and p==original
    assert prepare_clean_narration(out) is out and len(saved)==1


def test_caption_only_citation_reuses_audio_but_requires_realignment(monkeypatch):
    monkeypatch.setattr(store,'save',lambda x:x)
    p={'narrations':[dict(id='n1',enabled=True,text='Police arrive.',audio='keep.wav',audio_hash='ok',duration=3,
                         cues=[{'text':'At 138 seconds, police arrive.'}],caption_version=4)]}
    out=prepare_clean_narration(p)
    n=out['narrations'][0]
    assert n['audio']=='keep.wav' and n['audio_hash']=='ok'
    assert n['caption_version']==0 and n['cues']==[]
