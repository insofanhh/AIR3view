import copy
import json
import hashlib

import pytest

from backend import reaction_visual as visual, reaction_visual_recovery as recovery
from backend import reaction_review as review, render_cache
from backend.reaction_cops import validate_plan
from tests.test_reaction_review import fixture, outline, writer


def partition_fixture():
    p,raw=fixture(length=6,count=2)
    p['settings'].update(reaction_scene_duration_mode='range',reaction_scene_min_seconds=2,
                         reaction_scene_max_seconds=2)
    p['shots']=[dict(start=i*2,end=i*2+2) for i in range(6)]
    rows=[]
    for i,(a,b,c) in enumerate([(0,3,0),(3,6,0),(6,7,1),(7,10,1),(10,12,1)]):
        rows.append(dict(raw['selections'][c],id=f'sel{i}',start=a,end=b,
            narration='__write_review__' if i in (0,3) else '',
            commentary_span=3 if i==0 else 2 if i==3 else 1,
            section='opening' if i==0 else 'ending' if i==3 else 'development'))
    schedule=validate_plan({**raw,'selections':rows},p,check_text=False)
    return p,schedule


def test_repartition_real_solver_keeps_all_footage_and_exact_cut_setting():
    p,schedule=partition_fixture()
    before=copy.deepcopy(schedule)
    result,repairs=recovery.fit_schedule(schedule,p,lambda *a:None,lambda:None)
    assert repairs[0]['operation']=='repartition_visual_windows'
    assert result['selections'][0]['commentary_span']==2
    assert result['selections'][2]['commentary_span']==3
    assert result['selections'][2]['section']=='ending'
    assert [(r['start'],r['end'],r['evidence']) for r in result['selections']]==[
        (r['start'],r['end'],r['evidence']) for r in before['selections']]
    cuts=visual.plan({**p,'story_plan':result},recovery.logical_schedule(result))['clips']
    assert len(cuts)==6 and all(round((c['end']-c['start'])*30)==60 for c in cuts)
    assert schedule==before


def test_last_window_can_repartition_with_previous_window():
    p,schedule=partition_fixture()
    result=recovery._repartition(schedule,dict(window_id='sel3',required_frames=150,
        source_floor=210,prior_window_floor=0,prior_window_excerpt=None),p,lambda:None)
    assert result and result[1]['window_id']=='sel3'
    visual.plan({**p,'story_plan':result[0]},recovery.logical_schedule(result[0]))


def test_prefix_solver_cache_reuses_only_same_window_inputs(monkeypatch):
    p,schedule=partition_fixture()
    schedule=recovery.fit_schedule(schedule,p,lambda *a:None,lambda:None)[0]
    p['story_plan']=schedule
    real=visual._solve;calls=[]
    def counted(*a,**k):
        calls.append(1)
        return real(*a,**k)
    monkeypatch.setattr(visual,'_solve',counted)
    cache={};logical=recovery.logical_schedule(schedule)
    first=visual.plan(p,logical,window_cache=cache)
    assert len(calls)==2
    second=visual.plan(p,logical,window_cache=cache)
    assert len(calls)==2 and first['clips']==second['clips']
    p['story_plan']['selections'][2]['reason']='Updated current evidence focus'
    visual.plan(p,logical,window_cache=cache)
    assert len(calls)==3


def test_frame_threshold_from_real_log_accepts_578_to_375():
    p,raw=fixture(length=578/30,count=1)
    row=dict(raw['selections'][0],id='sel5',narration='__write_review__')
    proposed=recovery._shorten(dict(raw,selections=[row]),dict(required_frames=578,
        feasible_frames=375,row_ids=['sel5'],window_id='sel5'),p['settings'],p['reaction_cues'])
    assert proposed and round(proposed[0]['selections'][0]['end']*30)==375
    assert recovery._shorten(dict(raw,selections=[row]),dict(required_frames=578,
        feasible_frames=374,row_ids=['sel5']),p['settings'],p['reaction_cues']) is None


def test_two_reported_shortages_recover_in_one_long_schedule():
    p,raw=fixture(length=20,count=6)
    lengths=[672,578,600,600,600,600]
    total=sum(lengths)
    p['metadata']['duration']=total/30;p['settings']['summary_seconds']=total/30
    p['settings'].update(reaction_scene_duration_mode='range',reaction_scene_min_seconds=1,
                         reaction_scene_max_seconds=2.5)
    rows=[];shots=[];cursor=0
    for index,frames in enumerate(lengths):
        cue=p['reaction_cues'][index];cue.update(start=cursor/30,end=(cursor+frames)/30)
        p['source_speech']['items'][index].update(start=cue['start'],end=cue['end'])
        count=5 if index==0 else 4
        for j in range(count):
            a=cursor+round(frames*j/count);b=cursor+round(frames*(j+1)/count)
            rows.append(dict(raw['selections'][index],id=f'sel{len(rows)}',start=a/30,end=b/30,
                narration='__write_review__' if j==0 else '',commentary_span=count if j==0 else 1,
                section='opening' if index==0 and j==0 else 'ending' if index==5 and j==0 else 'development'))
        shot_lengths=([65]*7+[68,149] if index==0 else [37]*4+[38]*4+[278] if index==1 else [60]*10)
        point=cursor
        for length in shot_lengths:
            shots.append(dict(start=point/30,end=(point+length)/30));point+=length
        assert point==cursor+frames
        cursor+=frames
    p['shots']=shots
    schedule=validate_plan({**raw,'selections':rows},p,check_text=False)
    result,repairs=recovery.fit_schedule(schedule,p,lambda *a:None,lambda:None)
    reductions={r['window_id']:(r['old_seconds'],r['new_seconds']) for r in repairs if r['operation']=='fit_visual_capacity'}
    assert reductions['sel0']==pytest.approx((22.4,598/30))
    assert reductions['sel5']==pytest.approx((578/30,12.5))
    assert sum(r['end']-r['start'] for r in result['selections'])==pytest.approx((total-277)/30)
    assert [r['evidence'] for r in result['selections']]==[r['evidence'] for r in schedule['selections']]
    visual.plan({**p,'story_plan':result},recovery.logical_schedule(result))


def test_measured_short_voice_reuses_text_and_keeps_other_completed_audio(tmp_path):
    from tests.test_reaction_review import ready
    from backend.scene_duration_repair import adjust
    from backend.plan_first import contract_check
    p,raw=fixture(length=12,count=6)
    locked=validate_plan(review.prepare(raw,p),p,check_text=False)
    locked=review.write(locked,p,outline(p),writer,tmp_path,lambda *a:None,lambda:None)
    p=ready(p,locked);before=copy.deepcopy(p)
    candidate=adjust(p,p['narrations'][0],22.8,p['narrations'][0]['target_duration'])
    assert candidate is not None
    assert candidate['narrations'][0]['text']==before['narrations'][0]['text']
    assert 22.8<=candidate['narrations'][0]['target_duration']<=23.2
    assert candidate['narrations'][1:]==before['narrations'][1:]
    assert candidate['settings']==before['settings'] and p==before
    contract_check(candidate)


def test_measured_review_cannot_drop_evidence_to_fit_extremely_short_voice(tmp_path):
    from tests.test_reaction_review import ready
    from backend.scene_duration_repair import adjust
    p,raw=fixture(length=12,count=6)
    locked=validate_plan(review.prepare(raw,p),p,check_text=False)
    locked=review.write(locked,p,outline(p),writer,tmp_path,lambda *a:None,lambda:None)
    p=ready(p,locked);before=copy.deepcopy(p)
    assert adjust(p,p['narrations'][0],1,p['narrations'][0]['target_duration']) is None
    assert p==before


def test_synthesis_reuses_one_real_wav_after_picture_fit(tmp_path,monkeypatch):
    import shutil
    from backend import providers,media,store,vieneu
    from tests.test_reaction_review import ready
    from backend.timeline import build_story
    if not shutil.which(media.FFMPEG):
        pytest.skip('FFmpeg required')
    p,raw=fixture(length=12,count=6)
    p['settings']['voice_speed']=1.25
    locked=validate_plan(review.prepare(raw,p),p,check_text=False)
    locked=review.write(locked,p,outline(p),writer,tmp_path,lambda *a:None,lambda:None)
    p=ready(p,locked);text=p['narrations'][0]['text'];others=copy.deepcopy(p['narrations'][1:])
    monkeypatch.setattr(store,'project_dir',lambda *a:tmp_path)
    monkeypatch.setattr(store,'save',lambda p:p)
    calls=[]
    def generate(settings,text,destination,*a,**k):
        calls.append(text)
        destination.parent.mkdir(exist_ok=True)
        media.run([media.FFMPEG,'-y','-f','lavfi','-i','sine=frequency=880:sample_rate=48000',
            '-t','28.5',destination])
        return destination
    monkeypatch.setattr(vieneu,'generate',generate)
    monkeypatch.setattr(providers,'ask_ai',lambda *a:pytest.fail('must not rewrite approved words'))
    monkeypatch.setattr(providers,'transcribe',lambda *a,**k:[])
    result=providers.synthesize(p,lambda *a:None,lambda:None,only_id=p['narrations'][0]['id'])
    assert calls==[text]
    assert result['narrations'][0]['text']==text
    assert result['narrations'][1:]==others
    fit=result['voice_repair_state'][p['narrations'][0]['id']]['fit']
    assert fit['tempo']==1.25 and fit['final_duration']==pytest.approx(22.8,abs=.04)
    build_story(result,strict=True,visual=False)


def test_factual_contract_survives_retry_skips_accepted_and_repeated_text(tmp_path):
    p,raw=fixture(length=12,count=6)
    locked=validate_plan(review.prepare(raw,p),p,check_text=False)
    calls=[]
    def reject(prompt,*args):
        calls.append(args[-1].__name__)
        answer=writer(prompt,*args)
        if args[-1] is review.ReviewCheck:
            answer['items'][0].update(valid=False,issue='A promised radio is not a request.',
                supported_facts=[dict(statement='An officer says they will get a radio.',source_cue_ids=['c0'])],
                forbidden_claims=['Someone requested a radio.'],required_points=['Preserve the promised action.'])
        return answer
    with pytest.raises(ValueError,match='5 lượt'):
        review.write(copy.deepcopy(locked),p,outline(p),reject,tmp_path,lambda *a:None,lambda:None)
    assert calls.count('ReviewText')==5 and calls.count('ReviewCheck')==1
    seen=[]
    def fix(prompt,*args):
        answer=writer(prompt,*args)
        if args[-1] is review.ReviewText:
            entries=json.loads(prompt.split('REQUESTED: ')[1].split('\nPREVIOUS HOST')[0])
            seen.extend(e['id'] for e in entries)
            if entries[0]['id']=='sel0':
                constraints=entries[0]['repair_constraints']
                assert constraints['previous_attempts']==5
                assert constraints['evidence_contract'][0]['forbidden_claims']==['Someone requested a radio.']
                assert 'Conservative evidence repair' in prompt
                answer['items'][0]['text']='An officer says they will get a radio. That statement does not establish who requested it.'
        return answer
    result=review.write(copy.deepcopy(locked),p,outline(p),fix,tmp_path,lambda *a:None,lambda:None)
    assert seen[0]=='sel0' and 'sel5' not in seen and 'sel10' not in seen
    assert result['selections'][0]['narration'].startswith('An officer says')
    # Changing unrelated outline metadata cannot force all accepted voices back
    # through writing/review; each entry is keyed by its actual relevant focus.
    changed=outline(p);changed['title']='Another faithful title'
    review.write(copy.deepcopy(locked),p,changed,lambda *a:pytest.fail('must reuse'),tmp_path,lambda *a:None,lambda:None)


def test_exact_v020_review_cache_migrates_without_rewriting_approved_text(tmp_path):
    from backend.hook_policy import slots
    p,raw=fixture()
    locked=validate_plan(review.prepare(raw,p),p,check_text=False)
    entries=[s for s in slots(locked) if s['narration'].strip()]
    old_identity=dict(version=3,outline=outline(p),slots=entries,language=p['settings']['language'],
        rule=p['settings']['draft_rule'],model=p['settings']['model'],provider=p['settings']['provider'])
    cache=tmp_path/'reaction-review-cache';cache.mkdir()
    key=hashlib.sha256(json.dumps(old_identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    text='The accusation remains disputed. A check is needed before treating that claim as confirmed.'
    (cache/(key+'.json')).write_text(json.dumps(dict(generation=4,accepted={e['id']:text for e in entries})),'utf-8')
    result=review.write(copy.deepcopy(locked),p,outline(p),lambda *a:pytest.fail('must migrate exact reviewed cache'),
                        tmp_path,lambda *a:None,lambda:None)
    assert result['selections'][0]['narration']==text
    assert len(list(cache.glob('*.window.json')))==len(entries)


def test_v020_failure_feedback_is_used_on_first_new_writer_attempt(tmp_path):
    from backend.hook_policy import slots
    p,raw=fixture();locked=validate_plan(review.prepare(raw,p),p,check_text=False)
    entries=[s for s in slots(locked) if s['narration'].strip()]
    identity=dict(version=3,outline=outline(p),slots=entries,language=p['settings']['language'],
        rule=p['settings']['draft_rule'],model=p['settings']['model'],provider=p['settings']['provider'])
    cache=tmp_path/'reaction-review-cache';cache.mkdir()
    key=hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    (cache/(key+'.json')).write_text(json.dumps(dict(generation=5,accepted={},failures=[
        dict(ids=['sel0'],feedback=['sel0: Possible charges are not filed charges.'])])),'utf-8')
    checked=[]
    def inspect(prompt,*args):
        if args[-1] is review.ReviewText:
            entry=json.loads(prompt.split('REQUESTED: ')[1].split('\nPREVIOUS HOST')[0])[0]
            assert entry['repair_constraints']['previous_attempts']==5
            assert entry['repair_constraints']['rejected'][0]['issue'].endswith('Possible charges are not filed charges.')
            checked.append(True)
        return writer(prompt,*args)
    review.write(locked,p,outline(p),inspect,tmp_path,lambda *a:None,lambda:None)
    assert checked


def test_review_contract_cannot_cite_future_or_unknown_facts(tmp_path):
    p,raw=fixture();locked=validate_plan(review.prepare(raw,p),p,check_text=False)
    def invalid(prompt,*args):
        answer=writer(prompt,*args)
        if args[-1] is review.ReviewCheck:
            for v in answer['items']:
                v.update(valid=True,supported_facts=[dict(statement='Invented outcome',source_cue_ids=['unknown_future'])])
        return answer
    with pytest.raises(ValueError,match='invalid evidence review contract'):
        review.write(locked,p,outline(p),invalid,tmp_path,lambda *a:None,lambda:None)
    assert all(not json.loads(f.read_text('utf-8'))['accepted'] for f in (tmp_path/'reaction-review-cache').glob('*.window.json'))


@pytest.mark.parametrize('mutes,expected',[
    ([(0,5),(5,10)],True), ([(0,5),(5.01,10)],False), ([(2,10)],False),
    ([(0,20)],True), ([],False)])
def test_source_audio_decode_skips_only_complete_mute_coverage(mutes,expected):
    p,_=fixture()
    assert render_cache.source_silent(p,dict(source_mutes=[dict(start=a,end=b) for a,b in mutes]),
        dict(start=0,end=10)) is expected
