import pytest
from backend.models import Settings
from backend.render import layout, write_subtitles


@pytest.mark.parametrize('preset,margin',[('reference',10),('classic',10),('reference',24)])
def test_multiline_subtitle_anchors_above_source_bottom(tmp_path,preset,margin):
    settings=Settings(layout_preset=preset,subtitle_bottom_margin=margin,subtitle_position='inside').model_dump()
    timeline={'cues':[{'start':0,'end':2,'text':'A subtitle long enough to wrap across two lines in this video layout, remaining above the bottom edge.'}]}
    write_subtitles(tmp_path,{'settings':settings},timeline,{'index':1,'start':0,'end':2,'duration':2})
    ass=(tmp_path/'part-001.ass').read_text('utf-8-sig')
    style=next(line for line in ass.splitlines() if line.startswith('Style: Sub,')).split(',')
    geometry=layout(settings)
    assert int(style[18])==2  # ASS bottom-center, grows upward for multiple lines.
    assert 1920-int(style[21])==geometry['top']+geometry['height']-margin
    assert r'\N' in ass


def test_below_video_position_remains_top_anchored(tmp_path):
    settings=Settings(subtitle_position='below').model_dump()
    write_subtitles(tmp_path,{'settings':settings},{'cues':[]},{'index':1,'start':0,'end':2,'duration':2})
    ass=(tmp_path/'part-001.ass').read_text('utf-8-sig')
    style=next(line for line in ass.splitlines() if line.startswith('Style: Sub,')).split(',')
    assert int(style[18])==8 and int(style[21])==1480


def test_news_slide_title_toggle_and_subtitle_gap(tmp_path):
    base = Settings(layout_preset='news_slide', title='A new point of view',
                    subtitle_position='below', subtitle_top_margin=500).model_dump()
    part = {'index': 1, 'start': 0, 'end': 2, 'duration': 2}
    timeline = {'cues': []}
    write_subtitles(tmp_path, {'settings': base}, timeline, part)
    ass = (tmp_path / 'part-001.ass').read_text('utf-8-sig')
    style = next(line for line in ass.splitlines() if line.startswith('Style: Sub,')).split(',')
    assert 'Style: Title,Georgia,' in ass
    assert 'Style: Sub,Segoe UI,' in ass
    assert layout(base)['top'] == 80
    assert int(style[21]) == 1580
    assert int(style[19]) == int(style[20]) == 130
    assert r'\pos(130,1210)' in ass
    assert 'A new point of view' in ass
    assert 'Nguồn' not in ass and 'Ngày đăng' not in ass

    hidden = {**base, 'show_title': False, 'subtitle_top_margin': 60}
    write_subtitles(tmp_path, {'settings': hidden}, timeline, part)
    ass = (tmp_path / 'part-001.ass').read_text('utf-8-sig')
    style = next(line for line in ass.splitlines() if line.startswith('Style: Sub,')).split(',')
    assert int(style[21]) == 1140
    assert int(style[19]) == int(style[20]) == 130
    assert 'A new point of view' not in ass
