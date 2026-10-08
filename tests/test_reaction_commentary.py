import copy
import json
import shutil

import pytest

from backend.models import Settings
from backend.media import FFMPEG
from backend.reaction_commentary import allocate, audit, duration, slot, validate
from backend.reaction_cops import _clean_footage, _effective_duration, _fill_from_full_source, validate_plan


def project(length=2, count=24, gap=1):
    settings = Settings(editorial_mode='reaction_cops', production_workflow='plan_first',
                        narration_style='storytelling', output_mode='single', language='English',
                        summary_seconds=length*count, reaction_commentary_count=3,
                        reaction_scene_duration_mode='range', reaction_scene_min_seconds=1,
                        reaction_scene_max_seconds=length, provider='openai').model_dump()
    cues = [dict(id=str(i), start=i*(length+gap), end=i*(length+gap)+length,
                 text=f'Witness statement number {i}.', words=[]) for i in range(count)]
    roles = [dict(cue=i, start=c['start'], end=c['end'], text=c['text'], role='participant',
                  confidence=.9, priority=.8, evidence='real exchange', hook_score=0)
             for i, c in enumerate(cues)]
    return dict(id='test', settings=settings, metadata={'duration':cues[-1]['end'], 'has_audio':True},
                transcript=cues, source_transcript=cues, reaction_cues=cues,
                source_speech={'version':3, 'items':roles}, source={}, summary='', scenes=[],
                narrations=[], hooks=[], exports=[], preview_exports=[], warnings=[])


def planned(p):
    hook = dict(start=0, end=0, title='', reason='Off', original_audio=False, narration='')
    blocks = _clean_footage(p)
    budget = _effective_duration(p, blocks, hook)
    p['reaction_duration_budget'] = budget
    return validate_plan(_fill_from_full_source(p, {'title':'Confirmed development', 'selections':[]},
                                              blocks, hook, budget), p, check_text=False)


def ready(p, plan):
    from backend.hook_policy import slots
    from backend.plan_first import geometry
    from backend.story import duration_plan_manifest, plan_fingerprint
    p['story_plan'] = plan
    p['narrations'] = [dict(id='story-'+s['id'], segment_id=s['id'], start=s['start'],
                           target_duration=round(s['end']-s['start']-.04, 3), evidence=s['evidence'],
                           text=s['narration'], enabled=True, section='development', part=s['part'],
                           audio='', audio_hash='', duration=0, cues=[], caption_version=4)
                       for s in slots(plan) if s['narration'].strip()]
    fp = plan_fingerprint(p)
    p['plan_fingerprint'] = fp
    p['duration_plan'] = {**duration_plan_manifest(plan, p), 'status':'ready',
                          'input_fingerprint':fp, 'geometry':geometry(plan), 'schedule':copy.deepcopy(plan)}
    return p


@pytest.mark.parametrize('length,count', [(1,48), (2,24), (3,16)])
def test_short_cuts_keep_evidence_and_three_feasible_windows(length, count):
    p = project(length, count)
    before = _clean_footage(p)
    plan = planned(p)
    assert [(r['start'], r['end']) for r in plan['selections']] == [
        (b['start'], b['end']) for b in before]
    windows = audit(plan)
    assert len(windows) == 3
    assert all(8 <= w['seconds'] <= 25 for w in windows)
    assert all(len(w['scene_ids']) > 1 for w in windows)
    ids = [i for w in windows for i in w['scene_ids']]
    assert len(ids) == len(set(ids))
    first = next(i for i, r in enumerate(plan['selections']) if r['narration'])
    combined = slot(plan, first)
    from backend.reaction_cops import _evidence_ids
    assert len(_evidence_ids(combined['evidence'])) == plan['selections'][first]['commentary_span']
    assert combined['end']-combined['start'] == pytest.approx(duration(plan, first))
    assert combined['end']-combined['start'] < combined['source_ranges'][-1]['end']-combined['start']


def test_long_source_with_over_500_short_cuts_keeps_ten_commentary_points():
    p = project(length=1, count=600, gap=0)
    p['settings']['reaction_commentary_count'] = 10
    plan = planned(p)
    assert len(plan['selections']) == 600
    assert len(audit(plan)) == 10
    assert all(8 <= w['seconds'] <= 25 for w in audit(plan))


@pytest.mark.parametrize('invalid', ['overlap', 'part', 'overflow', 'too_long'])
def test_window_validation_rejects_overlap_parts_overflow_and_long_voice(invalid):
    plan = planned(project())
    owners = [i for i, r in enumerate(plan['selections']) if r['narration']]
    i = owners[0]
    if invalid == 'overlap':
        plan['selections'][i+1]['narration'] = 'Unexpected second voice.'
    elif invalid == 'part':
        plan['selections'][i+1]['part'] = 2
    elif invalid == 'overflow':
        plan['selections'][owners[-1]]['commentary_span'] = 500
    else:
        plan['selections'][i]['end'] += 30
    with pytest.raises(ValueError):
        validate(plan)


def test_isolated_cuts_are_original_audio_instead_of_impossible_commentary():
    rows = [dict(start=i*100, end=i*100+2, part=1, narration='__write__', priority=.8) for i in range(4)]
    allocate(rows, [0, 3], 2)
    assert all(not r['narration'] for r in rows)


def test_measured_voice_extends_only_its_window_without_touching_cuts():
    from backend.scene_duration_repair import adjust
    from backend.plan_first import contract_check
    p = project()
    plan = planned(p)
    # Reserve only the first window so adjacent footage can be borrowed.
    for r in plan['selections']:
        r['narration'] = ''
        r.pop('commentary_span', None)
    plan['selections'][0].update(narration='The exchange changes. The question remains unresolved.', commentary_span=4)
    ready(p, plan)
    before = copy.deepcopy(p)
    result = adjust(p, p['narrations'][0], 10.5, 7.96)
    assert result is not None and p == before
    assert result['story_plan']['selections'][0]['commentary_span'] == 6
    assert result['narrations'][0]['target_duration'] == 11.96
    assert [(r['start'], r['end']) for r in result['story_plan']['selections']] == [
        (r['start'], r['end']) for r in before['story_plan']['selections']]
    contract_check(result)
    result['story_plan']['selections'][0]['commentary_span'] = 5
    with pytest.raises(ValueError, match='Mốc cảnh khác'):
        contract_check(result)


def test_long_voice_cannot_extend_into_an_unbuildable_picture_window(monkeypatch):
    from backend.reaction_commentary import extend_to_audio
    from backend.reaction_visual import VisualConstraintError
    # Import before patching: recovery owns a direct reference to the real
    # solver; do not let this test's stub become its module-level dependency.
    from backend import reaction_visual_recovery
    p=project()
    plan=planned(p)
    for r in plan['selections']:
        r['narration']=''
        r.pop('commentary_span',None)
    plan['selections'][0].update(narration='The exchange changes. The question remains unresolved.',commentary_span=4)
    ready(p,plan)
    p['shots']=[dict(start=0,end=p['metadata']['duration'])]
    before=copy.deepcopy(p)
    calls=[]
    def unavailable(candidate,*args):
        calls.append(candidate['story_plan']['selections'][0]['commentary_span'])
        raise VisualConstraintError('insufficient_distinct_shots','No matching pictures')
    monkeypatch.setattr('backend.reaction_visual.plan',unavailable)
    diagnostics=[]
    assert extend_to_audio(p,p['narrations'][0],10.5,diagnostics) is None
    assert calls and min(calls)==6
    assert any('insufficient_distinct_shots' in d for d in diagnostics)
    assert p==before


def test_voice_can_cross_cuts_and_original_subtitles_return_when_it_ends():
    from backend.timeline import build_story
    from backend.providers import voice_hash
    p = project()
    plan = planned(p)
    ready(p, plan)
    for n in p['narrations']:
        n.update(text='The question stays open. The exchange continues.', audio='voices/test.wav', duration=7,
                 cues=[dict(id='ai', start=0, end=7, text='The question stays open. The exchange continues.')])
        n['audio_hash'] = voice_hash(n, p['settings'])
    timeline = build_story(p, strict=True, visual=False)
    assert len(timeline['clips']) == len(plan['selections'])
    assert len(timeline['voices']) == 3
    assert all(v['end']-v['start'] > 2 for v in timeline['voices'])
    for v in timeline['voices']:
        assert not any(c['speaker']=='original' and c['start']<v['end']-.01 and c['end']>v['start']+.01
                       for c in timeline['cues'])
        assert any(c['speaker']=='original' and c['start']>=v['end']-.01 for c in timeline['cues'])
    assert timeline['narration_mix']['original_ratio'] == pytest.approx(1-21/48)
    p['narrations'][0]['duration'] = p['narrations'][0]['target_duration']+2
    p['narrations'][0]['audio_hash'] = voice_hash(p['narrations'][0], p['settings'])
    with pytest.raises(ValueError, match='dài hơn cảnh'):
        build_story(p, strict=True, visual=False)


@pytest.mark.parametrize('language', ['English', 'Vietnamese'])
def test_full_planner_writer_contract_preserves_short_cuts(monkeypatch, tmp_path, language):
    from backend import providers, store
    from backend.reaction_cops import plan_reaction
    from backend.plan_first import contract_check
    p = project()
    p['settings']['language'] = language
    # A separate synthetic camera shot for every verified exchange. The new
    # pre-writing visual reserve must run rather than requiring a media file.
    p['shots']=[dict(start=c['start'],end=p['reaction_cues'][i+1]['start']
                     if i+1<len(p['reaction_cues']) else p['metadata']['duration'])
                for i,c in enumerate(p['reaction_cues'])]
    monkeypatch.setattr(store, 'project_dir', lambda _: tmp_path)
    calls = []
    def ai(prompt, _frames, _settings, _folder, _check, schema):
        calls.append(schema.__name__)
        if schema.__name__ == 'OptimizedBatch':
            return {'cues':[{'source_cue_ids':[c['id']], 'text':c['text']} for c in p['transcript']], 'warnings':[]}
        if schema.__name__ == 'SpeechRoles':
            return {'items':[{k:r[k] for k in ('cue','role','confidence','priority','evidence','hook_score')}
                             for r in p['source_speech']['items']]}
        if schema.__name__ == 'ReactionFootagePlan':
            return dict(title='Confirmed development', synopsis='Witness exchanges', last_confirmed_event='Last exchange',
                        selections=[dict(start=0, end=2, part=1, section='development', reason='Verified',
                                         priority=.8, source_cue_ids=['0'], keep_original=False)])
        if schema.__name__ == 'ScheduledNarration':
            entries = json.loads(prompt.split('\nREQUESTED SLOTS: ',1)[1].split('\nACCEPTED',1)[0])
            assert all(e['end']-e['start']>=8 for e in entries)
            assert all(len(e['source_ranges'])>1 for e in entries)
            text = ('The question is still unresolved. The exchange stays focused on what can be confirmed.'
                    if language=='English' else 'Câu hỏi vẫn chưa được giải đáp. Cuộc trao đổi tiếp tục làm rõ điều đã xác nhận.')
            return {'items':[{'id':e['id'], 'text':text} for e in entries]}
        if schema.__name__ == 'ReactionTitle':
            return {'title':'Confirmed development'}
        raise AssertionError(schema.__name__)
    monkeypatch.setattr(providers, 'ask_ai', ai)
    result = plan_reaction(p, lambda *_: None, lambda: None)
    assert len(result['narrations']) == 3
    assert len(result['story_plan']['selections']) == 24
    assert calls.count('ScheduledNarration') == 1
    assert len(result['duration_plan']['commentary_windows']) == 3
    contract_check(result)


def test_failed_point_does_not_repeat_completed_points_on_resume(tmp_path):
    from backend.story_schedule import write_scheduled
    p = project()
    plan = planned(p)
    ids = [str(i) for i, r in enumerate(plan['selections']) if r['narration']]
    requested = []
    def ai(prompt, *_):
        entries = json.loads(prompt.split('\nREQUESTED SLOTS: ',1)[1].split('\nACCEPTED',1)[0])
        requested.append([e['id'] for e in entries])
        return {'items':[{'id':e['id'], 'text':'Welcome back.' if e['id']==ids[-1]
                          else 'The question remains unresolved. The exchange continues.'} for e in entries]}
    with pytest.raises(ValueError, match='6 lượt sửa'):
        write_scheduled(plan, p, ai, tmp_path, lambda *_:None, lambda:None, locked=True)
    assert requested[0] == ids
    assert all(group == [ids[-1]] for group in requested[1:])
    resumed = []
    def fixed(prompt, *_):
        entries = json.loads(prompt.split('\nREQUESTED SLOTS: ',1)[1].split('\nACCEPTED',1)[0])
        resumed.extend(e['id'] for e in entries)
        return {'items':[{'id':e['id'], 'text':'The check does not settle the question. The exchange continues.'}
                         for e in entries]}
    result = write_scheduled(plan, p, fixed, tmp_path, lambda *_:None, lambda:None, locked=True)
    assert resumed == [ids[-1]]
    assert sum(bool(r['narration']) for r in result['selections']) == 3


@pytest.mark.skipif(not shutil.which(FFMPEG), reason='FFmpeg required')
def test_real_render_voice_survives_short_cuts_and_source_sound_returns(tmp_path, monkeypatch):
    import av
    import numpy as np
    from backend import store
    from backend.media import run, probe
    from backend.providers import voice_hash
    from backend.timeline import build_story
    from backend.render import render_part
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'DB', tmp_path/'test.sqlite3')
    store.init()
    saved = store.create('Multi-cut commentary test', {'kind':'upload', 'file':'source.mp4'})
    p = project(length=1, count=12)
    p['id'] = saved['id']
    p['source'] = saved['source']
    p['settings'].update(reaction_commentary_count=1, render_encoder='cpu', duck_volume=.2)
    folder = store.project_dir(p['id'])
    run([FFMPEG, '-y', '-f', 'lavfi', '-i', 'testsrc2=size=240x180:rate=30',
         '-f', 'lavfi', '-i', 'sine=frequency=220:sample_rate=48000', '-t', '23',
         '-c:v', 'libx264', '-c:a', 'aac', folder/'source.mp4'])
    run([FFMPEG, '-y', '-f', 'lavfi', '-i', 'sine=frequency=880:sample_rate=48000',
         '-t', '8', folder/'voice.wav'])
    p['metadata'] = probe(folder/'source.mp4')
    ready(p, planned(p))
    n = p['narrations'][0]
    n.update(text='The question stays unresolved. The exchange continues.', audio='voice.wav', duration=8)
    n['audio_hash'] = voice_hash(n, p['settings'])
    timeline = build_story(p, strict=True, visual=False)
    assert len(timeline['clips']) == 12
    output = render_part(p, timeline, timeline['parts'][0], folder, lambda:None, width=360)
    with av.open(str(store.asset(p['id'], output['file']))) as container:
        samples = np.concatenate([f.to_ndarray()[0] for f in container.decode(audio=0)])
    def amplitude(freq, a, b):
        data = samples[int(a*48000):int(b*48000)]
        return abs(np.sum(data*np.exp(-2j*np.pi*freq*np.arange(len(data))/48000)))/len(data)
    assert amplitude(880, .2, .8) > .01
    assert amplitude(880, 4.2, 4.8) > .01  # Still audible after four visual cuts.
    assert amplitude(880, 9.2, 9.8) < .001
    assert amplitude(220, 4.2, 4.8)/amplitude(220, 9.2, 9.8) == pytest.approx(.2, abs=.03)
    assert probe(store.asset(p['id'], output['file']))['duration'] == pytest.approx(12, abs=.1)
