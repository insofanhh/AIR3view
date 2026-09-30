import shutil
import pytest
from backend import store
from backend.models import Settings
from backend.media import FFMPEG,run,probe
from backend.render import render


@pytest.mark.skipif(not shutil.which(FFMPEG), reason='FFmpeg required')
def test_news_slide_renders_white_panel_and_source_blur(tmp_path, monkeypatch):
    import av
    from backend.timeline import build
    from backend.render import render_part

    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'DB', tmp_path / 'test.sqlite3')
    store.init()
    project = store.create('Slide test', {'kind': 'upload', 'file': 'source.mp4'})
    folder = store.project_dir(project['id'])
    run([FFMPEG, '-y', '-f', 'lavfi', '-i', 'color=blue:s=320x240:r=30:d=1',
         '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-t', '1',
         '-c:v', 'libx264', '-c:a', 'aac', folder / 'source.mp4'])
    project['metadata'] = probe(folder / 'source.mp4')
    project['settings'].update(layout_preset='news_slide', show_title=True,
                               title='Cảnh sát chặn nhầm kẻ trộm', subtitle_color='#111111',
                               subtitle_position='below', source_subtitle_blur=True,
                               render_encoder='cpu', output_mode='single')
    project['transcript'] = [{'id': 'c1', 'start': .1, 'end': .9,
                              'text': 'Diễn biến bất ngờ.', 'speaker': 'original'}]
    timeline = build(project)
    result = render_part(project, timeline, timeline['parts'][0], folder,
                         lambda: None, width=360, video_only=True)
    ass = store.asset(project['id'], result['ass']).read_text('utf-8-sig')
    assert 'Style: Title,Georgia,' in ass and 'Style: Sub,Segoe UI,' in ass
    assert 'Cảnh sát chặn nhầm kẻ trộm' in ass and 'Diễn biến bất ngờ.' in ass
    with av.open(str(store.asset(project['id'], result['file']))) as video:
        frame = next(video.decode(video=0)).to_ndarray(format='rgb24')
    assert frame[450, 200].min() > 230  # white title/caption panel
    assert frame[450, 5, 0] > 150 and frame[450, 5, 2] < 130  # red side rail
    assert frame[180, 200, 2] > 150  # source footage remains in the upper pane


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
def test_rolling_captions_replace_prior_update_without_overlapping():
    from backend.render import display_cues
    cues = [
        {'id':'early','start':0,'end':4,'text':'Wait','speaker':'original','words':[]},
        {'id':'update','start':2,'end':6,'text':'Wait for me','speaker':'original','words':[]},
        {'id':'ai','start':5,'end':5.5,'text':'A new turn','speaker':'ai','words':[]},
    ]
    visible = display_cues(cues, 0, 6)
    assert [(c['id'],c['start'],c['end']) for c in visible] == [
        ('early',0,2),('update',2,5),('ai',5,5.5),('update',5.5,6)]
    assert all(a['end']<=b['start'] for a,b in zip(visible,visible[1:]))
