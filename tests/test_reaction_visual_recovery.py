import copy
import json

import pytest

from backend import reaction_visual as visual
from backend import reaction_visual_recovery as recovery
from backend.reaction_cops import validate_plan
from backend.plan_first import contract_check
from tests.test_reaction_review import fixture, ready, writer, outline
from tests.test_reaction_visual import example


def shortage():
    """Same numbers as the user's log, without assuming its unseen source."""
    p,raw = fixture(length=22.4,count=1)
    p['settings'].update(reaction_scene_duration_mode='range',reaction_scene_min_seconds=1,
                         reaction_scene_max_seconds=2.5)
    p['reaction_duration_budget'] = dict(part_count=1,requested_seconds=22.4,
        effective_seconds=22.4,available_seconds=22.4,limiting_factor='none')
    # 8 short shots + one long shot. Their capped duration is exactly 18.6s.
    points = [i*60 for i in range(9)] + [672]
    points[8] = 483
    p['shots'] = [dict(start=a/30,end=b/30) for a,b in zip(points,points[1:])]
    rows = []
    for i in range(4):
        rows.append({**raw['selections'][0], 'id':f'sel{i}', 'start':i*5.6,'end':(i+1)*5.6,
                     'narration':'__write_review__' if i==0 else '',
                     'commentary_span':4 if i==0 else 1,'section':'opening' if i==0 else 'development'})
    schedule = validate_plan({**raw,'selections':rows},p,check_text=False)
    return p,schedule


def test_reported_shortage_reserved_before_writing_preserves_every_citation():
    p,schedule = shortage()
    before = copy.deepcopy(schedule)
    with pytest.raises(visual.VisualConstraintError) as exc:
        visual.plan({**p,'story_plan':schedule},recovery.logical_schedule(schedule))
    assert exc.value.context['required_frames']==672
    assert exc.value.context['feasible_frames']==558
    result,repairs = recovery.fit_schedule(schedule,p,lambda *a:None,lambda:None)
    assert sum(r['end']-r['start'] for r in result['selections'])==pytest.approx(18.6)
    assert len(repairs)==1 and repairs[0]['window_id']=='sel0'
    assert [r['evidence'] for r in result['selections']]==[r['evidence'] for r in before['selections']]
    assert [r['id'] for r in result['selections']]==[r['id'] for r in before['selections']]
    assert schedule==before  # candidate is transactional
    edit = visual.plan({**p,'story_plan':result},recovery.logical_schedule(result))
    assert len(edit['clips'])==9
    assert all(1<=c['end']-c['start']<=2.5 for c in edit['clips'])
    assert p['settings']['summary_seconds']==22.4


def test_independent_bodycam_exchanges_allow_gapped_excerpts_but_no_equal_split():
    p,logical,_ = example(total=4,source=10,shot_seconds=10)
    p['reaction_cues'] = [dict(id='c0',start=0,end=2,text='First exchange.'),
                          dict(id='c1',start=5,end=7,text='Separate response.')]
    p['source_speech']['items'] = [dict(role='participant',confidence=.9) for _ in range(2)]
    r = p['story_plan']['selections'][0]
    r['evidence']='SRC_CUES=["c0","c1"]\nTwo confirmed exchanges.'
    p['settings'].update(reaction_scene_min_seconds=2,reaction_scene_max_seconds=2)
    cuts = visual.plan(p,logical)['clips']
    assert [(c['source_start'],c['source_end']) for c in cuts]==[(0,2),(5,7)]
    assert cuts[0]['shot_id']==cuts[1]['shot_id']
    assert cuts[0]['evidence_ids']!=cuts[1]['evidence_ids']
    for cut in cuts:
        assert cut['end']-cut['start']==2
    # Adjacent source halves, even distinct cues, remain forbidden.
    p['reaction_cues'][1].update(start=2,end=4)
    with pytest.raises(visual.VisualConstraintError):
        visual.plan(p,logical)


def test_uncited_nearby_participant_cannot_be_used_as_unrelated_padding():
    p,logical,_ = example(total=4,source=10,shot_seconds=2)
    p['reaction_cues'] = [dict(id='c0',start=0,end=2,text='Incident one.'),
                          dict(id='c1',start=2,end=10,text='Another incident.')]
    p['source_speech']['items'] = [dict(role='participant',confidence=.9) for _ in range(2)]
    p['story_plan']['selections'][0]['end']=2
    with pytest.raises(visual.VisualConstraintError):
        visual.plan(p,logical)


def test_recover_existing_multirow_review_invokes_writer_review_and_keeps_voice_settings(tmp_path):
    p,schedule = shortage()
    # Give it accepted text/audio, mimicking a project created on v0.1.19.
    schedule['selections'][0]['narration']='The response disputes the claim. Further checking is necessary.'
    p = ready(p,schedule)
    p['reaction_review_outline'] = outline(p)
    original = copy.deepcopy(p)
    with pytest.raises(visual.VisualConstraintError) as exc:
        visual.plan(p,recovery.logical_schedule(schedule))
    calls=[]
    def ai(prompt,*args):
        calls.append(args[-1].__name__)
        return writer(prompt,*args)
    result = recovery.recover(p,exc.value,ai,tmp_path,lambda *a:None,lambda:None)
    assert calls==['ReviewText','ReviewCheck']
    assert result['narrations'][0]['target_duration']==pytest.approx(18.56)
    assert result['narrations'][0]['audio']==''
    assert result['narrations'][0]['evidence'].startswith('SRC_CUES=')
    assert result['settings']==original['settings']
    assert p==original
    contract_check(result)
    visual.plan(result,recovery.logical_schedule(result['story_plan']))


def test_failed_factual_review_or_cancel_never_publishes_changed_story(tmp_path):
    p,schedule = shortage()
    schedule['selections'][0]['narration']='The response disputes the claim. Further checking is necessary.'
    p=ready(p,schedule)
    original=copy.deepcopy(p)
    with pytest.raises(visual.VisualConstraintError) as exc:
        visual.plan(p,recovery.logical_schedule(schedule))
    class Cancelled(Exception): pass
    def cancelled(*args): raise Cancelled()
    with pytest.raises(Cancelled):
        recovery.recover(p,exc.value,cancelled,tmp_path,lambda *a:None,lambda:None)
    assert p==original


def test_frame_rounding_can_shorten_to_real_feasible_frames():
    p,schedule = shortage()
    p['settings'].update(reaction_scene_min_seconds=2,reaction_scene_max_seconds=2)
    result,_ = recovery.fit_schedule(schedule,p,lambda *a:None,lambda:None)
    assert recovery.logical_schedule(result)[-1]['end']==18
    cuts = visual.plan({**p,'story_plan':result},recovery.logical_schedule(result))['clips']
    assert all(round((c['end']-c['start'])*30)==60 for c in cuts)


def test_large_source_shortage_is_bounded_and_preserves_original_budget():
    p,schedule=shortage()
    p['shots']=[dict(start=0,end=22.4)]
    original=copy.deepcopy(p)
    with pytest.raises(visual.VisualConstraintError):
        recovery.fit_schedule(schedule,p,lambda *a:None,lambda:None)
    assert p==original


def test_automatic_mode_does_not_detect_shots_or_rewrite(monkeypatch):
    p,schedule=shortage()
    p['settings']['reaction_scene_duration_mode']='auto'
    monkeypatch.setattr('backend.media.ensure_shots',lambda *a:pytest.fail('must not detect'))
    result,repairs=recovery.fit_schedule(schedule,p,lambda *a:None,lambda:None)
    assert result==schedule and repairs==[]


def test_cached_blocked_state_keeps_machine_readable_context():
    p,schedule=shortage()
    p['story_plan']=schedule
    logical=recovery.logical_schedule(schedule)
    timeline=dict(clips=logical,planned=True,warnings=[])
    for _ in range(2):
        with pytest.raises(visual.VisualConstraintError) as exc:
            visual.apply(p,timeline,strict=True)
        assert exc.value.context['row_ids']==['sel0','sel1','sel2','sel3']
        assert exc.value.context['feasible_frames']==558


def test_single_continuous_cited_cue_never_becomes_fake_a_b():
    p,logical,_=example(total=8,source=12,shot_seconds=12)
    with pytest.raises(visual.VisualConstraintError):
        visual.plan(p,logical)


def test_preflight_in_full_review_build_runs_before_text_requests(monkeypatch,tmp_path):
    from backend import reaction_review
    p,_ = fixture()
    p['settings'].update(reaction_scene_duration_mode='range',reaction_scene_min_seconds=1,
                         reaction_scene_max_seconds=2.5)
    calls=[]
    def unavailable(*args):
        calls.append('reserve')
        raise visual.VisualConstraintError('test','unavailable')
    monkeypatch.setattr(recovery,'reserve',unavailable)
    def ai(prompt,*args):
        calls.append(args[-1].__name__)
        return writer(prompt,*args)
    with pytest.raises(visual.VisualConstraintError,match='unavailable'):
        reaction_review.build(p,ai,tmp_path,lambda *a:None,lambda:None)
    assert calls==['ReviewOutline','reserve']


def test_unchanged_review_audio_is_preserved_on_local_repair(tmp_path):
    p,schedule=shortage()
    p['metadata']['duration']=32.4
    p['settings']['summary_seconds']=32.4
    p['reaction_duration_budget'].update(requested_seconds=32.4,effective_seconds=32.4,available_seconds=32.4)
    p['reaction_cues'].append(dict(id='c1',start=22.4,end=32.4,text='The check remains pending.'))
    p['source_speech']['items'].append(dict(p['source_speech']['items'][0],start=22.4,end=32.4))
    p['shots'] += [dict(start=22.4+2*i,end=24.4+2*i) for i in range(5)]
    row=copy.deepcopy(schedule['selections'][-1])
    for i in range(2):
        schedule['selections'].append(dict(row,id=f'sel{i+4}',start=22.4+5*i,end=27.4+5*i,
            narration='The check remains pending. The claim is still unconfirmed.' if i==0 else '',
            commentary_span=2 if i==0 else 1,section='ending' if i==0 else 'development',
            evidence='SRC_CUES=["c1"]\nThe check remains pending.'))
    schedule['selections'][0]['narration']='The response disputes the claim. Further checking is necessary.'
    p=ready(p,schedule)
    before=copy.deepcopy(p['narrations'][1])
    calls=[]
    def ai(prompt,*args):
        if args[-1].__name__=='ReviewText':
            entries=json.loads(prompt.split('REQUESTED: ')[1].split('\nPREVIOUS HOST')[0])
            calls.extend(e['id'] for e in entries)
        return writer(prompt,*args)
    with pytest.raises(visual.VisualConstraintError) as exc:
        visual.plan(p,recovery.logical_schedule(schedule))
    q=recovery.recover(p,exc.value,ai,tmp_path,lambda *a:None,lambda:None)
    assert calls==['sel0']
    assert q['narrations'][1]==before
    contract_check(q)


def test_shared_evidence_windows_rebalance_without_crossing_incident():
    p,raw=fixture(length=6,count=1)
    p['settings'].update(summary_seconds=12,reaction_scene_duration_mode='range',
                         reaction_scene_min_seconds=2,reaction_scene_max_seconds=3)
    p['metadata']['duration']=12
    p['reaction_cues'][0]['end']=12
    p['source_speech']['items'][0]['end']=12
    p['shots']=[dict(start=0,end=12)]
    rows=[dict(raw['selections'][0],id='sel0',start=0,end=6,narration='__write_review__',section='opening'),
          dict(raw['selections'][0],id='sel1',start=6,end=12,narration='__write_review__',section='ending')]
    schedule=dict(raw,selections=rows)
    proposed=recovery._join(schedule,{'window_id':'sel0','required_frames':180},p['settings'])
    assert proposed is not None
    assert proposed[0]['selections'][0]['commentary_span']==2
    rows[1]['evidence']='SRC_CUES=["other_incident"]\nAnother incident'
    assert recovery._join(schedule,{'window_id':'sel0','required_frames':180},p['settings']) is None


def test_cached_manifest_cannot_fake_adjacent_halves_with_new_cue_ids():
    p,logical,_=example(total=4,source=10,shot_seconds=10)
    p['reaction_cues']=[dict(id='c0',start=0,end=2,text='First.'),dict(id='c1',start=5,end=7,text='Second.')]
    p['source_speech']['items']=[dict(role='participant',confidence=.9) for _ in range(2)]
    p['story_plan']['selections'][0]['evidence']='SRC_CUES=["c0","c1"]\nDistinct statements'
    edit=visual.plan(p,logical)
    edit['clips'][1].update(source_start=2,source_end=4,evidence_ids=['c1'])
    with pytest.raises(visual.VisualConstraintError,match='same_shot'):
        visual.validate(p,logical,edit)


def test_error_reports_feasible_path_not_sum_of_overlapping_candidates():
    p,logical,_=example(total=8,source=12,shot_seconds=12)
    p['reaction_cues']=[dict(id='c0',start=0,end=6,text='One long question.'),
                       dict(id='c1',start=1,end=12,text='Overlapping caption of that question.')]
    p['source_speech']['items']=[dict(role='participant',confidence=.9) for _ in range(2)]
    p['story_plan']['selections'][0]['evidence']='SRC_CUES=["c0","c1"]\nSame exchange'
    with pytest.raises(visual.VisualConstraintError) as exc:
        visual.plan(p,logical)
    assert exc.value.context['candidate_capacity_frames']>exc.value.context['feasible_frames']
    assert exc.value.context['feasible_frames']==60


def test_previous_repairs_cannot_shrink_window_indefinitely():
    p,schedule=shortage()
    p['visual_repair_history']=[{'window_id':'sel0'} for _ in range(2)]
    before=copy.deepcopy(p)
    with pytest.raises(visual.VisualConstraintError):
        recovery.fit_schedule(schedule,p,lambda *a:None,lambda:None)
    assert p==before


def test_run_all_repairs_existing_blocked_plan_before_tts(tmp_path,monkeypatch):
    from backend import app,store
    p,schedule=shortage()
    schedule['selections'][0]['narration']='The response disputes the claim. Further checking is necessary.'
    p=ready(p,schedule)
    monkeypatch.setattr(store,'project_dir',lambda *a:tmp_path)
    saves=[]
    monkeypatch.setattr(store,'save',lambda project:saves.append(copy.deepcopy(project)) or project)
    monkeypatch.setattr(app.providers,'ask_ai',writer)
    result=app.prepare_visual_stage(p,lambda *a:None,lambda:None,repair=True)
    assert saves[0]['visual_edit']['status']=='blocked'
    assert saves[-1]['visual_edit']['status']=='ready'
    assert result['narrations'][0]['target_duration']==18.56
    assert result['narrations'][0]['audio']==''
    contract_check(result)


def test_final_render_does_not_silently_rewrite_existing_voice(tmp_path,monkeypatch):
    from backend import app,store
    p,schedule=shortage()
    schedule['selections'][0]['narration']='The response disputes the claim. Further checking is necessary.'
    p=ready(p,schedule)
    monkeypatch.setattr(store,'save',lambda p:p)
    monkeypatch.setattr(app.providers,'ask_ai',lambda *a:pytest.fail('must not silently rewrite'))
    with pytest.raises(visual.VisualConstraintError):
        app.prepare_visual_stage(p,lambda *a:None,lambda:None)


def test_rebuilding_missing_shots_does_not_publish_failed_recovery(monkeypatch):
    p,schedule=shortage()
    p.pop('shots')
    before=copy.deepcopy(p)
    def detect(candidate,*args,persist=True):
        assert persist is False
        candidate['shots']=[dict(start=0,end=22.4)]
    monkeypatch.setattr('backend.media.ensure_shots',detect)
    monkeypatch.setattr('backend.store.save',lambda *a:pytest.fail('unvalidated recovery must not save'))
    with pytest.raises(visual.VisualConstraintError):
        recovery.fit_schedule(schedule,p,lambda *a:None,lambda:None)
    assert p==before


def test_shot_detector_can_build_private_index_without_saving(tmp_path,monkeypatch):
    from backend import media,store
    import scenedetect
    p,_=shortage()
    p.pop('shots')
    monkeypatch.setattr(store,'project_dir',lambda *a:tmp_path)
    monkeypatch.setattr(store,'asset',lambda *a:tmp_path/'source.mp4')
    monkeypatch.setattr(scenedetect,'detect',lambda *a,**k:[])
    saves=[]
    monkeypatch.setattr(store,'save',lambda project:saves.append(copy.deepcopy(project)))
    assert media.ensure_shots(p,persist=False)==[dict(start=0,end=22.4)]
    assert saves==[]
    p.pop('shots')
    media.ensure_shots(p)
    assert saves[0]['shots']==p['shots']
