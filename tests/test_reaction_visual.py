import copy
import json
import shutil

import av
import numpy as np
import pytest

from backend import media, reaction_visual, render, render_cache, store
from backend.reaction_visual import VisualConstraintError, apply, plan, validate
from backend.story import plan_fingerprint, plan_is_current
from backend.timeline import build_story, slice_clips
from tests.test_reaction_review import fixture, prepare, ready, writer, outline, write
from backend import reaction_cops


def example(total=8, source=12, shot_seconds=3):
    p, raw = fixture(length=source, count=1)
    p['settings'].update(reaction_scene_duration_mode='range',
                         reaction_scene_min_seconds=1, reaction_scene_max_seconds=2)
    row = {**raw['selections'][0], 'id':'sel0', 'end':total, 'narration':'An evidenced review.'}
    p['story_plan'] = {**raw, 'selections':[row]}
    p['shots'] = [dict(start=a, end=min(source, a+shot_seconds))
                  for a in range(0, source, shot_seconds)]
    logical = [dict(kind='highlight', start=0, end=total, source_start=0,
                    source_end=total, part=1, segment_id='sel0', section='opening')]
    timeline = dict(clips=logical, voices=[], cues=[], duration=total,
                    parts=[dict(index=1,start=0,end=total,duration=total)], planned=True, warnings=[])
    return p, logical, timeline


def test_distinct_real_shots_exact_duration_and_same_locked_audio():
    p, logical, timeline = example()
    old = copy.deepcopy(p['story_plan'])
    result = apply(p, timeline, strict=True)
    assert result['audio_clips'] == logical
    assert result['duration'] == 8
    assert p['story_plan'] == old
    assert [(c['source_start'],c['source_end']) for c in result['clips']] == [(0,2),(3,5),(6,8),(9,11)]
    assert len({c['shot_id'] for c in result['clips']}) == 4
    assert all(c['end']-c['start'] == 2 for c in result['clips'])
    assert p['visual_edit']['beats'][0]['text'] == 'An evidenced review.'
    # Export boundaries slice picture and audio independently, without changing
    # their absolute locked tracks or pretending a partial export is a new shot.
    visual = slice_clips(result, 1, 7)
    audio = slice_clips(result, 1, 7, 'audio_clips')
    assert visual[-1]['source_start'] == 9
    assert audio[0]['source_start'] == 1 and audio[0]['source_end'] == 7
    assert sum(c['end']-c['start'] for c in visual) == 6


def test_same_continuous_shot_is_not_made_into_fake_a_b_cuts():
    p, logical, timeline = example(shot_seconds=12)
    with pytest.raises(VisualConstraintError, match='insufficient_distinct_shots'):
        apply(p, timeline, strict=True)
    assert p['visual_edit']['status'] == 'blocked'
    from backend.batches import retry_policy
    # Timestamp 429 must not accidentally match the generic transient 429 rule.
    assert retry_policy('[visual:insufficient_distinct_shots] sel429', 0) == (0,0)
    result = apply(p, timeline)
    assert result['clips'] == logical and result['visual_edit']['status'] == 'blocked'


def test_short_remainder_rebalances_at_voice_window_not_per_logical_row():
    p, raw = fixture(length=3, count=2)
    p['settings'].update(reaction_scene_duration_mode='range',
                         reaction_scene_min_seconds=2, reaction_scene_max_seconds=2.5)
    # Both rows cite the same real exchange: 3s per logical row cannot satisfy
    # 2–2.5s; their combined 6s can, across three real shots.
    for i,r in enumerate(raw['selections']):
        r.update(id=f'sel{i}',evidence='SRC_CUES=["c0","c1"]\nVerified exchange',
                 narration='An evidenced review.' if i==0 else '')
    raw['selections'][0]['commentary_span'] = 2
    p['story_plan'] = raw
    p['shots'] = [dict(start=a,end=a+2) for a in range(0,6,2)]
    logical = [dict(kind='highlight',start=r['start'],end=r['end'],source_start=r['start'],
                    source_end=r['end'],segment_id=r['id'],part=1,section=r['section']) for r in raw['selections']]
    cuts = plan(p,logical)['clips']
    assert len(cuts) == 3 and all(c['end']-c['start'] == 2 for c in cuts)


def test_exact_frame_infeasibility_does_not_pad_or_speed_voice():
    p, logical, _ = example(total=91/30)
    p['settings'].update(reaction_scene_min_seconds=1,reaction_scene_max_seconds=1)
    with pytest.raises(VisualConstraintError, match='frame_budget'):
        plan(p,logical)


def test_evidence_from_other_incident_or_excluded_speech_cannot_fill_shot_budget():
    p, logical, _ = example()
    p['reaction_cues'] = [dict(id='c0',start=0,end=3,text='Incident one.'),
                          dict(id='c1',start=3,end=12,text='Different incident.')]
    p['source_speech']['items'] += [dict(role='participant',confidence=.9)]
    logical[0]['source_end'] = 8
    # The planned selection is too broad; excluded speech must never become
    # an eligible picture to solve timing, even when it has a genuine shot.
    p['source_speech']['items'][1]['role'] = 'commentary'
    with pytest.raises(VisualConstraintError, match='insufficient_distinct_shots'):
        plan(p,logical)
    p['source_speech']['items'][0]['confidence'] = .4
    with pytest.raises(VisualConstraintError, match='evidence'):
        plan(p,logical)


@pytest.mark.parametrize('mode', ['review','original','dubbed'])
def test_windows_keep_all_audio_profiles_and_evidence_beats(mode):
    p, logical, _ = example()
    p['settings'].update(reaction_commentary_count=0 if mode=='review' else 2,
                         reaction_audio_mode='dubbed' if mode=='dubbed' else 'original')
    if mode=='dubbed':
        p['story_plan']['selections'][0]['narration'] = ''
        p['story_plan']['dialogue_dubs'] = [dict(id='dub0',selection_ids=['sel0'])]
    edit = plan(p,logical)
    validate(p,logical,edit)
    assert len(edit['clips']) == 4
    assert all(c['evidence_ids'] == ['c0'] for c in edit['clips'])


def test_locked_story_timeline_keeps_voice_cues_original_mapping_and_count(tmp_path):
    p, raw = fixture(length=8,count=3)
    out = write(reaction_cops.validate_plan(prepare(raw,p),p,check_text=False),p,outline(p),
                writer,tmp_path,lambda *a:None,lambda:None)
    p = ready(p,out)
    base = build_story(p,strict=True)
    p['shots'] = [dict(start=a,end=a+2) for a in range(0,24,2)]
    p['settings'].update(reaction_scene_duration_mode='range',reaction_scene_min_seconds=1,
                         reaction_scene_max_seconds=2.5)
    changed = build_story(p,strict=True)
    assert changed['voices'] == base['voices']
    assert changed['cues'] == base['cues']
    assert changed['source_mutes'] == base['source_mutes']
    assert changed['audio_clips'] == base['clips']
    assert len(changed['clips']) == 12
    assert p['plan_fingerprint'] == plan_fingerprint(p)


def test_hook_has_own_duration_and_may_repeat_source_without_fake_main_cut():
    p, logical, _ = example()
    hook = dict(kind='hook',start=0,end=3,source_start=9,source_end=12,
                segment_id='hook',section='hook',part=1)
    logical[0].update(start=3,end=11)
    logical.insert(0,hook)
    result = plan(p,logical)
    assert result['clips'][0] == hook
    assert all(c['end']-c['start'] == 2 for c in result['clips'][1:])


def test_cache_invalidation_and_legacy_fingerprint_migration_are_scoped():
    p, logical, _ = example()
    old = plan_fingerprint(p,legacy_visual=True)
    p.update(plan_fingerprint=old,duration_plan={'input_fingerprint':old})
    assert plan_is_current(p)
    assert p['duration_plan']['input_fingerprint'] == p['plan_fingerprint']
    edit = plan(p,logical)
    p['settings']['reaction_scene_max_seconds'] = 3
    assert plan_is_current(p)  # Accepted text/voice stays current.
    with pytest.raises(VisualConstraintError,match='stale'):
        validate(p,logical,edit)
    p['settings']['language'] = 'Vietnamese'
    assert not plan_is_current(p)
    p, _, _ = example()
    p['plan_fingerprint'] = plan_fingerprint(p,legacy_visual=True)
    p['settings']['reaction_commentary_count'] = 3
    assert not plan_is_current(p)  # Never migrate an already-stale old plan.


def test_preflight_rejects_before_tts_and_saves_blocked_state(tmp_path,monkeypatch):
    p, logical, timeline = example(shot_seconds=12)
    p['plan_fingerprint'] = plan_fingerprint(p)
    monkeypatch.setattr('backend.timeline.build_story',lambda *a,**k:copy.deepcopy(timeline))
    monkeypatch.setattr(media,'ensure_shots',lambda *a:None)
    saved=[]
    monkeypatch.setattr(store,'save',lambda project:saved.append(copy.deepcopy(project)) or project)
    with pytest.raises(VisualConstraintError):
        reaction_visual.prepare(p,lambda *a:None,lambda:None)
    assert saved[-1]['visual_edit']['status'] == 'blocked'


def test_earliest_complete_window_preserves_shots_needed_by_the_next_window():
    p, logical, _ = example(total=2,source=6,shot_seconds=3)
    row = copy.deepcopy(p['story_plan']['selections'][0])
    p['story_plan']['selections'].append({**row,'id':'sel1','start':2,'end':4})
    logical.append({**logical[0],'segment_id':'sel1','start':2,'end':4,'source_start':2,'source_end':4})
    p['shots'] = [dict(start=0,end=1),dict(start=1,end=2),dict(start=2,end=6)]
    cuts = plan(p,logical)['clips']
    assert [(c['source_start'],c['source_end']) for c in cuts] == [(0,1),(1,2),(2,4)]


@pytest.mark.parametrize('fault',['same_shot','wrong_evidence','wrong_window','missing_beat'])
def test_cached_manifest_is_revalidated_before_render(fault):
    p, logical, timeline = example()
    p['visual_edit'] = plan(p,logical)
    cuts = p['visual_edit']['clips']
    if fault=='same_shot':
        cuts[1].update(source_start=1,source_end=3,shot_id='shot0')
    elif fault=='wrong_evidence':
        cuts[1]['evidence_ids'] = ['unrelated']
    elif fault=='wrong_window':
        cuts[1]['window_id'] = 'other'
    else:
        cuts[1]['evidence_segments'] = []
    with pytest.raises(VisualConstraintError):
        apply(p,timeline,strict=True)


def test_real_render_picture_jumps_between_distinct_shots_audio_stays_continuous(tmp_path,monkeypatch):
    if not shutil.which(media.FFMPEG):
        pytest.skip('FFmpeg required')
    monkeypatch.setattr(store,'DATA',tmp_path)
    monkeypatch.setattr(store,'DB',tmp_path/'studio.sqlite3')
    store.init()
    base = store.create('Distinct evidence shots',dict(kind='upload',file='source.mp4'))
    folder = store.project_dir(base['id'])
    # Known source shots with a second audio frequency AFTER the logical audio
    # range. The final picture uses that late shot; the sound must stay 440Hz.
    args = [media.FFMPEG,'-y']
    for color in ('red','white','blue','green'):
        args += ['-f','lavfi','-i',f'color={color}:s=160x90:r=30:d=3']
    args += ['-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=8',
             '-f','lavfi','-i','sine=frequency=880:sample_rate=48000:duration=4',
             '-filter_complex','[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0[v];[4:a][5:a]concat=n=2:v=0:a=1[a]',
             '-map','[v]','-map','[a]','-c:v','libx264','-c:a','aac',folder/'source.mp4']
    media.run(args)
    p, logical, timeline = example()
    p = {**base, **p, 'id':base['id'], 'metadata':media.probe(folder/'source.mp4')}
    # Exercise the actual detector/backfill rather than trusting fabricated
    # fixed-duration shot IDs from a scheduler.
    p.pop('shots')
    media.ensure_shots(p)
    assert len(p['shots']) == 4
    media.run([media.FFMPEG,'-y','-f','lavfi','-i','sine=frequency=1320:sample_rate=48000:duration=2',folder/'voice.wav'])
    media.run([media.FFMPEG,'-y','-f','lavfi','-i','sine=frequency=660:sample_rate=48000:duration=1',folder/'music.wav'])
    timeline['voices'] = [dict(id='review',start=1,end=3,audio='voice.wav',text='An evidenced review.')]
    p['settings'].update(render_encoder='cpu',subtitles=False,title='',show_title=False,
                         original_volume=1,duck_volume=1,background_mode='solid',
                         music_file='music.wav',music_name='Music',music_duration=1,music_volume=.3)
    edited = apply(p,timeline,strict=True)
    folder_direct=folder/'direct';folder_direct.mkdir()
    out = render.render_part(p,edited,edited['parts'][0],folder_direct,lambda:None,width=360)
    path = store.asset(p['id'],out['file'])
    assert media.probe(path)['duration'] == pytest.approx(8,abs=.05)
    with av.open(str(path)) as c:
        frames=list(c.decode(video=0))
    colors=[frames[int(t*30)].to_ndarray(format='rgb24')[470,180].astype(float)
            for t in (.5,2.5,4.5,6.5)]
    assert colors[0][0] > colors[0][1]+100
    assert colors[1].min() > 200
    assert colors[2][2] > colors[2][0]+100
    assert colors[3][1] > colors[3][0]+50
    def amplitude(path,freq,begin=6.2):
        with av.open(str(path)) as c:
            samples=np.concatenate([f.to_ndarray().mean(axis=0) for f in c.decode(audio=0)])
            rate=c.streams.audio[0].rate
        y=samples[int(begin*rate):int((begin+.6)*rate)]
        return abs(np.mean(y*np.exp(-2j*np.pi*freq*np.arange(len(y))/rate)))
    assert amplitude(path,440) > 30*amplitude(path,880)
    assert amplitude(path,1320,1.2) > .01
    assert amplitude(path,660) > .001
    # Cached path must have the same picture manifest and independent PCM audio.
    monkeypatch.setattr(reaction_visual,'prepare',lambda p,*a:p)
    monkeypatch.setattr(render_cache,'build',lambda *a,**k:edited)
    render.render(p,lambda *a:None,lambda:None,preview=True)
    cached=store.asset(p['id'],p['preview_exports'][0]['file'])
    assert amplitude(cached,440) > 30*amplitude(cached,880)
    assert amplitude(cached,1320,1.2) == pytest.approx(amplitude(path,1320,1.2),rel=.05)
    assert amplitude(cached,660) == pytest.approx(amplitude(path,660),rel=.05)
    part=edited['parts'][0]
    key=render_cache.audio_key(p,edited,part)
    altered=copy.deepcopy(edited)
    altered['clips'][0]['source_start'] += .5
    assert render_cache.audio_key(p,altered,part) == key
