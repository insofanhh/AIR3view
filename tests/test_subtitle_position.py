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
