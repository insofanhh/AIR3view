import copy
import json
import pytest
from backend import store, source_captions, media
from backend.captions import align_existing
from backend.providers import digest


@pytest.fixture
def environment(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DATA',tmp_path)
    monkeypatch.setattr(store,'DB',tmp_path/'studio.sqlite3')
    store.init()
    p=store.create('Test',{'file':'source.mp4'})
    p['metadata']={'duration':1800,'has_audio':True}
    p['transcript']=[dict(id='a',start=10,end=11,text='Hello.'),dict(id='b',start=900,end=901,text='Unused.')]
    audio=store.project_dir(p['id'])/'audio.wav';audio.write_bytes(b'audio')
    monkeypatch.setattr(source_captions,'source_windows',lambda p:[(10,11)])
    crops=[]
    def run(args,**kwargs):
        crops.append(args);args[-1].write_bytes(b'cropped')
    monkeypatch.setattr(media,'run',run)
    return p,crops,audio


def test_only_visible_missing_source_is_aligned_and_offsets_are_restored(environment):
    p,crops,_=environment;calls=[];before=copy.deepcopy(p['transcript'][1])
    def recognize(path,settings,check):
        calls.append(path)
        return [{'words':[{'text':'Hello.','start':.25,'end':1.25}]}]
    stats,missing=source_captions.refresh_source(p,lambda *a:None,lambda:None,recognize,align_existing)
    assert stats['asr_calls']==1 and stats['transcribed_seconds']==1.5
    assert crops[0][crops[0].index('-ss')+1]=='9.750000'
    assert p['transcript'][0]['words']==[dict(text='Hello.',start=10.,end=11.)]
    assert p['transcript'][1]==before and missing==[]
    source_captions.refresh_source(p,lambda *a:None,lambda:None,recognize,align_existing)
    assert len(calls)==1


def test_empty_recognition_is_cached_across_reloads(environment):
    p,_,_=environment;calls=[];original=copy.deepcopy(p)
    def none(*args):calls.append(1);return []
    first,_=source_captions.refresh_source(p,lambda *a:None,lambda:None,none,align_existing)
    second,missing=source_captions.refresh_source(original,lambda *a:None,lambda:None,none,align_existing)
    assert len(calls)==1 and second['reused']==1 and missing==[0]
    original['transcript'][0]['text']='Edited.'
    source_captions.refresh_source(original,lambda *a:None,lambda:None,none,align_existing)
    assert len(calls)==2


def test_existing_source_words_or_translation_never_call_asr(environment):
    p,crops,_=environment
    p['source_transcript']=[{**p['transcript'][0],'words':[dict(text='Hello.',start=10,end=11)]}]
    unexpected=lambda *a:pytest.fail('must reuse existing words')
    source_captions.refresh_source(p,lambda *a:None,lambda:None,unexpected,align_existing)
    assert p['transcript'][0]['words']
    p['transcript'][0].update(text='Xin chào.',words=[])
    stats,missing=source_captions.refresh_source(p,lambda *a:None,lambda:None,unexpected,align_existing)
    assert missing==[0] and not crops


def test_old_full_source_cache_reused_without_asr(environment):
    p,_,audio=environment
    fingerprint=digest({'audio':[audio.stat().st_size,audio.stat().st_mtime_ns],'asr':p['settings']['asr_model'],'version':1})
    folder=audio.parent/'word-alignment';folder.mkdir()
    (folder/(fingerprint+'.json')).write_text(json.dumps([{'words':[dict(text='Hello.',start=10,end=11)]}]),encoding='utf8')
    stats,_=source_captions.refresh_source(p,lambda *a:None,lambda:None,lambda *a:pytest.fail('cache available'),align_existing)
    assert stats['reused']==1 and stats['asr_calls']==0


def test_no_used_source_captions_means_no_processing(environment,monkeypatch):
    p,crops,_=environment
    monkeypatch.setattr(source_captions,'source_windows',lambda p:[])
    stats,missing=source_captions.refresh_source(p,lambda *a:None,lambda:None,lambda *a:pytest.fail('unused'),align_existing)
    assert stats['asr_calls']==0 and not missing and not crops


def test_cancel_cleans_working_wav_without_marking_complete(environment):
    p,_,audio=environment
    def cancel(*a):raise media.Cancelled('stop')
    with pytest.raises(media.Cancelled):
        source_captions.refresh_source(p,lambda *a:None,lambda:None,cancel,align_existing)
    cache=audio.parent/'word-alignment/selected-v1'
    assert not (cache/'working.wav').exists() and not list(cache.glob('*.json'))


def test_input_chunks_bounded_to_thirty_seconds(environment,monkeypatch):
    p,crops,_=environment
    p['transcript']=[dict(id='long',start=0,end=80,text='A long statement.')]
    monkeypatch.setattr(source_captions,'source_windows',lambda p:[(0,80)])
    source_captions.refresh_source(p,lambda *a:None,lambda:None,lambda *a:[],align_existing)
    assert len(crops)==3
    assert all(float(args[args.index('-t')+1])<=30 for args in crops)


def test_timeline_mapping_excludes_ai_and_deduplicates_repeated_hook(monkeypatch):
    from backend import timeline
    monkeypatch.setattr(timeline,'build',lambda p:{
       'clips':[dict(kind='hook',start=0,end=5,source_start=100),
                dict(kind='highlight',start=5,end=15,source_start=200),
                dict(kind='highlight',start=15,end=20,source_start=100)],
       'cues':[dict(speaker='original',start=1,end=3),dict(speaker='ai',start=5,end=15),
               dict(speaker='original',start=16,end=18)]})
    assert source_captions.source_windows({})==[(101,103)]
