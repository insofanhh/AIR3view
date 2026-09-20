import shutil
import pytest
from backend import store
from backend.models import Settings
from backend.media import FFMPEG,run,probe
from backend.render import render


@pytest.mark.skipif(not shutil.which(FFMPEG), reason='FFmpeg required')
def test_later_part_input_seek_retains_correct_source_frame(tmp_path, monkeypatch):
    import av
    import numpy as np
    from backend.timeline import build
    from backend.render import render_part
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'DB', tmp_path/'test.sqlite3')
    store.init()
    p = store.create('Seek test', {'kind':'upload','file':'source.mp4'})
    folder = store.project_dir(p['id'])
    run([FFMPEG, '-y', '-f', 'lavfi', '-i', 'color=red:s=160x160:r=30:d=2',
         '-f', 'lavfi', '-i', 'color=blue:s=160x160:r=30:d=2',
         '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]', '-c:v', 'libx264', folder/'source.mp4'])
    p['metadata'] = probe(folder/'source.mp4')
    p['settings'].update(output_mode=None, part_durations=[2], title='', subtitles=False)
    timeline = build(p)
    result = render_part(p, timeline, timeline['parts'][1], folder, lambda:None, width=360)
    with av.open(str(store.asset(p['id'], result['file']))) as c:
        frame = next(c.decode(video=0)).to_ndarray(format='rgb24')
    rgb = frame[200:350,100:250].mean(axis=(0,1))
    assert rgb[2] > 200 and rgb[0] < 20  # second half is blue, never red
    assert abs(result['duration'] - 2) < .05


@pytest.mark.skipif(not shutil.which(FFMPEG),reason='FFmpeg required')
def test_real_ffmpeg_export_with_hook_and_unicode(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DATA',tmp_path)
    monkeypatch.setattr(store,'DB',tmp_path/'test.sqlite3')
    store.init()
    p=store.create('Bản thử tiếng Việt',{'kind':'upload','file':'source.mp4'})
    folder=store.project_dir(p['id'])
    run([FFMPEG,'-y','-f','lavfi','-i','testsrc2=size=320x240:rate=30','-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','3','-c:v','libx264','-c:a','aac',folder/'source.mp4'])
    p['metadata']=probe(folder/'source.mp4')
    p['settings'].update(output_mode=None, title='Câu chuyện thử nghiệm',hook_enabled=True,hook_start=1,hook_end=2,background_mode='blur',part_durations=[2,2])
    p['transcript']=[{'id':'c1','start':.2,'end':2.5,'text':'Xin chào, đây là phụ đề tiếng Việt.','speaker':'original'}]
    result=render(p,lambda *a:None,lambda:None,preview=True)
    exported=store.asset(p['id'],result['preview_exports'][0]['file'])
    meta=probe(exported)
    assert (meta['width'],meta['height'])==(360,640)
    assert abs(meta['duration']-2)<.05
    assert 'Xin chào' in store.asset(p['id'],result['preview_exports'][0]['srt']).read_text('utf-8-sig')


@pytest.mark.skipif(not shutil.which(FFMPEG), reason='FFmpeg required')
def test_hook_from_late_source_then_restart_is_separate_input(tmp_path, monkeypatch):
    import av
    import numpy as np
    from backend.timeline import build
    from backend.render import render_part
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'DB', tmp_path/'test.sqlite3')
    store.init()
    p = store.create('Reverse seek hook', {'kind':'upload','file':'source.mp4'})
    folder = store.project_dir(p['id'])
    run([FFMPEG, '-y', '-f', 'lavfi', '-i', 'color=red:s=160x160:r=30:d=2',
         '-f', 'lavfi', '-i', 'color=blue:s=160x160:r=30:d=2',
         '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]', '-c:v', 'libx264', folder/'source.mp4'])
    p['metadata'] = probe(folder/'source.mp4')
    p['settings'].update(output_mode=None, hook_enabled=True, hook_start=2, hook_end=3, title='', subtitles=False)
    timeline = build(p)
    result = render_part(p, timeline, timeline['parts'][0], folder, lambda: None, width=360)
    with av.open(str(store.asset(p['id'], result['file']))) as container:
        assert container.streams.video[0].sample_aspect_ratio == 1
        frames = [f.to_ndarray(format='rgb24') for f in container.decode(video=0)]
    colors = [frames[i][200:350,100:250].mean(axis=(0,1)) for i in (15,45,105)]
    assert colors[0][2] > 200 and colors[0][0] < 20  # hook: blue
    assert colors[1][0] > 200 and colors[1][2] < 20  # source restarts: red
    assert colors[2][2] > 200 and colors[2][0] < 20  # source continues: blue
    assert abs(result['duration'] - 5) < .05
