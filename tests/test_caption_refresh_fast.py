from pathlib import Path

from backend import captions, store


def test_refresh_skips_source_asr_when_word_timings_are_already_present(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'DB', tmp_path/'studio.sqlite3')
    store.init()
    p=store.create('Captions', {'kind':'upload','file':'source.mp4'})
    p['metadata']={'has_audio':True}
    p['transcript']=[{'id':'c1','start':0,'end':1,'text':'Hello.',
                      'words':[{'text':'Hello.','start':0,'end':1}]}]
    called=[]
    monkeypatch.setattr(captions, 'transcribe', lambda *args, **kwargs: called.append(1) or [])
    captions.refresh(p, lambda *a:None, lambda:None)
    assert called == []
