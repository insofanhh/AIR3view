import shutil
import pytest
from backend import store
from backend.providers import voice_hash
from backend.media import FFMPEG, run, probe
from backend.render import render


@pytest.mark.skipif(not shutil.which(FFMPEG),reason='FFmpeg required')
@pytest.mark.parametrize('mode,gated,background',[
    ('overlay',False,0),('insert',False,0),('overlay',True,0),
    ('overlay',False,.15),('overlay',True,.15),('overlay',True,.4)])
def test_real_voice_mix_across_part_boundary(tmp_path,monkeypatch,mode,gated,background):
    monkeypatch.setattr(store,'DATA',tmp_path)
    monkeypatch.setattr(store,'DB',tmp_path/'test.sqlite3')
    store.init()
    p=store.create('Voice timeline test',{'kind':'upload','file':'source.mp4'})
    folder=store.project_dir(p['id'])
    run([FFMPEG,'-y','-f','lavfi','-i','testsrc2=size=240x180:rate=30','-f','lavfi','-i','sine=frequency=220:sample_rate=48000','-t','4','-c:v','libx264','-c:a','aac',folder/'source.mp4'])
    run([FFMPEG,'-y','-f','lavfi','-i','sine=frequency=880:sample_rate=48000','-t','2',folder/'voice.wav'])
    p['metadata']=probe(folder/'source.mp4')
    p['settings'].update(output_mode=None, narration_mode=mode,duck_volume=background,part_durations=[2],title='Kiểm thử trộn giọng')
    n={'id':'n1','start':1,'text':'Test narration','audio':'voice.wav','duration':2,'enabled':True,'cues':[{'id':'v1','start':0,'end':2,'text':'Test narration'}]}
    n['audio_hash']=voice_hash(n,p['settings'])
    p['narrations']=[n]
    result=render(p,lambda *a:None,lambda:None,preview=True)
    output=store.asset(p['id'],result['preview_exports'][0]['file'])
    assert probe(output)['has_audio']
    assert abs(probe(output)['duration']-2)<.05
    assert 'Test narration' in store.asset(p['id'],result['preview_exports'][0]['srt']).read_text('utf-8-sig')
    if mode == 'overlay':
        # Separate frequencies verify the requested background gain while AI
        # stays audible and the moving test pattern continues playing.
        import av
        import numpy as np
        with av.open(str(output)) as container:
            samples = np.concatenate([f.to_ndarray()[0] for f in container.decode(audio=0)])
        def amplitude(freq, a, b):
            segment = samples[int(a*48000):int(b*48000)]
            t = np.arange(len(segment))/48000
            return abs(np.sum(segment*np.exp(-2j*np.pi*freq*t))) / len(segment)
        assert amplitude(220, 1.2, 1.8)/amplitude(220, .2, .8) == pytest.approx(background,abs=.02)
        assert amplitude(880, 1.2, 1.8) > .01
        with av.open(str(output)) as container:
            frames = [f.to_ndarray(format='rgb24') for f in container.decode(video=0)]
        assert np.mean(np.abs(frames[38][110:470].astype(float)-frames[48][110:470].astype(float))) > 1

        # The voice crosses the split; background gain must also survive it.
        from backend.timeline import build
        from backend.render import render_part
        timeline = build(p, strict=True)
        if gated:
            # Only the selected original-dialogue interval gets full volume.
            # The following narration pause stays at background volume.
            timeline['original_audio'] = [{'start':3,'end':3.5}]
        second = render_part(p, timeline, timeline['parts'][1], output.parent, lambda: None, width=360)
        with av.open(str(store.asset(p['id'], second['file']))) as container:
            samples = np.concatenate([f.to_ndarray()[0] for f in container.decode(audio=0)])
        assert amplitude(220, .2, .8)/amplitude(220, 1.1, 1.4) == pytest.approx(background,abs=.02)
        if gated:
            assert amplitude(220, 1.65, 1.9)/amplitude(220, 1.1, 1.4) == pytest.approx(background,abs=.02)
        assert amplitude(880, .2, .8) > .01
        assert amplitude(880, 1.2, 1.8) < amplitude(880, .2, .8) * .02
