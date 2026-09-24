import copy
from backend import providers, store
from backend.models import Settings
from backend.narration_language import wrong_language, repair_language


VN='Các cảnh sát đã được đưa đến một khu vực trong rừng để tìm những người mất tích.'


def test_clear_vietnamese_paragraph_is_rejected_for_english_only():
    assert wrong_language(VN,'English')
    assert not wrong_language(VN,'Vietnamese')
    assert not wrong_language('Nguyễn Thị Minh and Trần Văn Nam arrived in Hà Nội.','English')


def test_repair_updates_only_wrong_language_and_preserves_timing(monkeypatch, tmp_path):
    p={'id':'a'*32,'settings':Settings(language='English').model_dump(),
       'narrations':[dict(id='n0',enabled=True,text=VN,start=2,segment_id='sel0',target_duration=0,audio='old.wav'),
                     dict(id='n1',enabled=True,text='They searched the area.',audio='keep.wav')],
       'story_plan':{'selections':[dict(id='sel0',narration=VN,start=2,end=10)]}}
    original=copy.deepcopy(p)
    monkeypatch.setattr(store,'project_dir',lambda _:tmp_path)
    monkeypatch.setattr(store,'save',lambda x:x)
    monkeypatch.setattr(providers,'ask_ai',lambda *a:{'items':[{'id':'n0','text':'Officers searched the forest for the missing people.'}]})
    out=repair_language(p,lambda *a:None,lambda:None)
    assert out['narrations'][0]['start']==2
    assert out['narrations'][0]['audio_hash']==''
    assert out['narrations'][1]==original['narrations'][1]
    assert out['story_plan']['selections'][0]['narration']==out['narrations'][0]['text']
    assert p==original
