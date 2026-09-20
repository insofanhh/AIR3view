import copy
import shutil
import pytest
from backend.models import Settings, Cue
from backend.alignment import align_script
from backend.captions import align_existing
from backend.timeline import build
from backend.providers import voice_hash
from backend.render import caption_events, write_subtitles, render_part
from backend.media import FFMPEG, run, probe
from backend import store


def cue():
    return {'id':'c0','start':1,'end':4,'text':'Hello world', 'speaker':'original',
            'words':[{'text':'Hello','start':1,'end':2}, {'text':'world','start':3,'end':4}]}


def test_alignment_retains_word_anchors_and_model_roundtrip():
    c = cue()
    result = align_script(c['text'],c['words'],5)
    words = [w for c in result for w in c['words']]
    assert words == c['words']
    assert Cue.model_validate(c).model_dump()['words'] == c['words']


def test_only_current_word_colored_and_silence_plain():
    settings = Settings().model_dump()
    settings['subtitle_highlight_color']='#ff0000'
    events = caption_events(cue(), settings, {'start':0,'end':5})
    assert len(events)==3
    assert r'{\1c&H000000ff}Hello' in events[0]
    assert r'\1c' not in events[1]
    assert r'{\1c&H000000ff}world' in events[2]
    assert '0:00:02.00,0:00:03.00' in events[1]


def test_split_keeps_word_active_without_restarting_sentence():
    events = caption_events(cue(),Settings().model_dump(),{'start':1.5,'end':3.5})
    assert '0:00:00.00,0:00:00.50' in events[0]
    assert '0:00:01.50,0:00:02.00' in events[-1]
    assert r'}Hello{\1c' in events[0]
    assert r'}world{\1c' in events[-1]


def test_edited_or_unaligned_text_does_not_highlight_wrong_words():
    c=cue(); c['text']='A completely different sentence'
    assert r'\1c' not in ''.join(caption_events(c,Settings().model_dump(),{'start':0,'end':5}))
    out=align_existing([c],cue()['words'])
    assert out[0]['text']==c['text'] and out[0]['words']==[]


def test_hooks_and_voice_map_word_timing_without_changing_source():
    c=cue()
    settings=Settings().model_dump();settings.update(hook_enabled=True,hook_start=1,hook_end=3)
    n={'id':'n0','start':5,'duration':4,'audio':'v.wav','text':'Hello world','enabled':True,'cues':[copy.deepcopy(c)]}
    n['audio_hash']=voice_hash(n,settings)
    p={'metadata':{'duration':12},'settings':settings,'transcript':[c],'narrations':[n]}
    t=build(p,strict=True)
    first=t['cues'][0]
    assert first['words'][0]['start']==0
    ai=next(c for c in t['cues'] if c['speaker']=='ai')
    assert ai['words'][0]['start']==8
    assert p['transcript'][0]['words'][0]['start']==1


def test_line_wrapping_and_srt_stay_plain(tmp_path):
    c=cue();c['text']='Hello world'*8;c['words']=[]
    settings=Settings().model_dump();settings['subtitle_highlight']=False
    write_subtitles(tmp_path,{'settings':settings},{'cues':[cue()]},{'index':1,'start':0,'end':5,'duration':5})
    assert 'Hello world' in (tmp_path/'part-001.srt').read_text('utf-8-sig')
    subs = [line for line in (tmp_path/'part-001.ass').read_text('utf-8-sig').splitlines() if ',Sub,' in line]
    assert all(r'\1c' not in line for line in subs)


@pytest.mark.skipif(not shutil.which(FFMPEG),reason='FFmpeg required')
def test_real_render_highlight_moves_and_disappears_in_pause(tmp_path,monkeypatch):
    import av
    import numpy as np
    monkeypatch.setattr(store,'DATA',tmp_path);monkeypatch.setattr(store,'DB',tmp_path/'test.sqlite3');store.init()
    p=store.create('Karaoke',{'kind':'upload','file':'source.mp4'});folder=store.project_dir(p['id'])
    run([FFMPEG,'-y','-f','lavfi','-i','color=black:s=160x160:r=30:d=5','-c:v','libx264',folder/'source.mp4'])
    p['metadata']=probe(folder/'source.mp4')
    p['settings'].update(output_mode=None, title='',subtitle_highlight_color='#ff0000',subtitle_size=70,source_subtitle_blur=True)
    p['transcript']=[cue()]
    timeline=build(p);result=render_part(p,timeline,timeline['parts'][0],folder,lambda:None,width=360)
    samples={}
    with av.open(str(store.asset(p['id'],result['file']))) as container:
        for f in container.decode(video=0):
            if f.pts is None: continue
            if round(f.time*30) in (45,75,105): samples[round(f.time*30)]=f.to_ndarray(format='rgb24')
    def pixels(frame):
        f=frame.astype(float)
        mask=(f[:,:,0]>120)&(f[:,:,0]>f[:,:,1]*1.8)&(f[:,:,0]>f[:,:,2]*1.8)
        return np.where(mask)[1]
    first,pause,second=[pixels(samples[t]) for t in (45,75,105)]
    assert len(first)>20 and len(second)>20 and len(pause)==0
    assert first.mean()<second.mean()


def test_highlight_keeps_line_breaks_and_escapes_text():
    text = 'First extraordinarily lengthy sentence with more wonderful words at the end'
    tokens = text.split()
    c = {'start':0,'end':len(tokens),'text':text,'words':[{'text':w,'start':i,'end':i+1} for i,w in enumerate(tokens)]}
    events = caption_events(c,Settings().model_dump(),{'start':0,'end':len(tokens)})
    assert len(events)==len(tokens)
    assert all(r'\N' in event for event in events)
    for i,event in enumerate(events):
        assert '}' + tokens[i] + r'{\1c' in event
