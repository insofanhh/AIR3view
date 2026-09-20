import copy
import pytest
from backend.models import Settings
from backend.providers import voice_hash
from backend.timeline import build, slice_clips
from backend.render import safe_text, srt_time
from backend.media import parse_srt


def project(duration=125):
    return {'metadata': {'duration': duration}, 'settings': Settings().model_dump(), 'narrations': [], 'transcript': []}


def add_voice(p, start=10, duration=4):
    n = {'id': 'n'+str(len(p['narrations'])), 'start': start, 'text': 'Một câu lời dẫn.', 'enabled': True, 'audio': 'voices/test.wav', 'duration': duration, 'cues': [{'id': 'c1', 'start': 0, 'end': duration, 'text': 'Một câu lời dẫn.'}]}
    n['audio_hash'] = voice_hash(n, p['settings'])
    p['narrations'].append(n)
    return n


def test_hook_preserves_entire_source_after_teaser():
    p = project()
    p['settings'].update(hook_enabled=True, hook_start=80, hook_end=85)
    t = build(p, True)
    assert t['duration'] == 130
    assert [(c['source_start'], c['source_end']) for c in t['clips']] == [(80, 85), (0, 125)]
    assert t['parts'][-1]['end'] == t['duration']


def test_insert_retains_all_source_without_gaps():
    p = project(20)
    p['settings']['narration_mode'] = 'insert'
    add_voice(p, 8, 4)
    add_voice(p, 15, 3)
    t = build(p, True)
    originals = [c for c in t['clips'] if c['kind'] == 'original']
    assert [(c['source_start'], c['source_end']) for c in originals] == [(0, 8), (8, 15), (15, 20)]
    assert t['duration'] == 27
    assert [v['start'] for v in t['voices']] == [8, 19]


def test_overlay_and_captions_shift_after_hook():
    p = project(20)
    p['settings'].update(hook_enabled=True, hook_start=0, hook_end=3)
    p['transcript'] = [{'id':'o1','start':9,'end':15,'text':'Original'}]
    add_voice(p, 10, 4)
    t = build(p, True)
    assert t['voices'][0]['start'] == 13
    original = [c for c in t['cues'] if c['speaker'] == 'original']
    assert [(c['start'],c['end']) for c in original] == [(12,13),(17,18)]


def test_stale_tts_cannot_be_exported():
    p = project()
    n = add_voice(p)
    n['text'] = 'Đã đổi câu'
    assert build(p)['warnings']
    with pytest.raises(ValueError, match='cần tạo giọng'):
        build(p, True)


def test_mixing_volume_does_not_invalidate_tts():
    p = project()
    add_voice(p)
    p['settings']['voice_volume'] = 1.5
    assert not build(p, True)['warnings']


@pytest.mark.parametrize('start,duration', [(124,3),(11,3)])
def test_voice_overflow_and_overlap_block_export(start,duration):
    p=project()
    add_voice(p,10,4)
    add_voice(p,start,duration)
    with pytest.raises(ValueError, match='chồng câu'):
        build(p,True)


def test_exact_parts_include_hook_and_short_last_part():
    p=project(121)
    p['settings'].update(hook_enabled=True,hook_start=10,hook_end=15,split_mode='exact')
    assert [x['duration'] for x in build(p)['parts']] == [60,60,6]


def test_natural_split_does_not_bisect_spoken_sentence():
    p=project()
    p['transcript']=[{'id':'c1','start':58,'end':62,'text':'A complete sentence'}]
    assert build(p)['parts'][0]['end'] == 62


def test_manual_lengths_never_drop_the_tail():
    p=project(125)
    p['settings'].update(part_durations=[30,40],split_mode='exact')
    t=build(p)
    assert [x['duration'] for x in t['parts']] == [30,40,55]
    assert t['parts'][-1]['end'] == 125


def test_part_slicing_maps_source_and_freeze_correctly():
    p=project(20)
    p['settings']['narration_mode']='insert'
    add_voice(p,8,4)
    chunks=slice_clips(build(p),10,15)
    assert [(x['kind'],x['source_start'],x['end']-x['start']) for x in chunks] == [('freeze',8,2),('original',8,3)]


def test_subtitle_parser_and_ass_injection():
    cues=parse_srt('1\n00:00:01,100 --> 00:00:02,200\nXin chào\n')
    assert cues[0]['start']==1.1
    assert cues[0]['text']=='Xin chào'
    assert '{' not in safe_text(r'{\pos(0,0)}attack')
    assert srt_time(60.123)=='00:01:00,123'


def test_invalid_hook_is_rejected():
    p=project(10)
    p['settings'].update(hook_enabled=True,hook_start=9,hook_end=3)
    with pytest.raises(ValueError):
        build(p)
