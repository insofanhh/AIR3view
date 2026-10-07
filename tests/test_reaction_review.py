import copy
import json
from pathlib import Path

import pytest

from backend import media, providers, reaction_cops, source_speech, store
from backend.models import Narration, Settings
from backend.hook_policy import slots
from backend.plan_first import contract_check, geometry
from backend.reaction_commentary import members
from backend.reaction_review import (ReviewOutline, ReviewText, ReviewCheck, active,
                                     prepare, validate, build, write, quality_issue, repair_voice,
                                     normalize_outline, visible_focus)
from backend.story import plan_fingerprint, duration_plan_manifest
from backend.timeline import build_story
from backend.voice_repair import DurationMismatchError, reaction_tail_gap


def fixture(length=10, count=3):
    settings = Settings(editorial_mode='reaction_cops', reaction_commentary_count=0,
                        narration_style='storytelling',production_workflow='plan_first',
                        output_mode='single',summary_seconds=length*count,language='English').model_dump()
    texts = ['Where did the money go?', 'I did not take the money.', 'We still need to check.']
    cues = [dict(id=f'c{i}',start=i*length,end=(i+1)*length,text=texts[i%3],speaker='original',words=[])
            for i in range(count)]
    roles = [dict(cue=i,start=c['start'],end=c['end'],text=c['text'],role='participant',confidence=.9,
                  priority=.8,evidence='In-scene exchange',hook_score=0) for i,c in enumerate(cues)]
    p = dict(id='review-test',settings=settings,metadata={'duration':length*count,'has_audio':True},
             source={'kind':'upload','file':'source.mp4'},reaction_cues=cues,
             source_speech={'version':source_speech.VERSION,'fingerprint':'test','items':roles},
             transcript=copy.deepcopy(cues),source_transcript=copy.deepcopy(cues),
             narrations=[],warnings=[],exports=[],preview_exports=[],scenes=[],summary='')
    plan = dict(title='The Money Question Remains Unresolved',synopsis='A disputed account is checked.',
                outcome='The check is still pending.',lesson='No invented lesson.',
                hook=dict(start=0,end=0,title='',reason='off',original_audio=False,narration=''),
                selections=[dict(start=c['start'],end=c['end'],part=1,section='development',priority=.8,
                                 reason='verified development',narration='',narration_offset=0,
                                 evidence='SRC_CUES='+json.dumps([c['id']])+'\n'+c['text']) for c in cues])
    return p,plan


def outline(p):
    return dict(title='The Money Question Remains Unresolved',central_question='Does the account settle the dispute?',
                chapters=[dict(focus='The question, denial and unresolved check',source_cue_ids=[c['id'] for c in p['reaction_cues']])])


def writer(prompt,images,settings,folder,check,schema):
    if schema is ReviewOutline:
        catalog=json.loads(prompt.split('\nCATALOG: ')[1])
        return dict(title='The Money Question Remains Unresolved',central_question='A disputed account needs checking.',
                    chapters=[dict(focus='Question, response and check',source_cue_ids=[c['id'] for c in catalog])])
    entries=json.loads(prompt.split('REQUESTED: ')[1].split('\nPREVIOUS HOST:')[0].split('\nPREVIOUS HOST (')[0].split('\nSTYLE:')[0].split('\nSTYLE (')[0])
    if schema is ReviewCheck:
        return {'items':[dict(id=e['id'],valid=True,issue='') for e in entries]}
    assert schema is ReviewText
    return {'items':[dict(id=e['id'],text='The response disputes the accusation. A check remains necessary before that claim can be treated as confirmed.',
                         source_cue_ids=[c['id'] for c in e['current_cues']]) for e in entries]}


def ready(p,plan):
    p=copy.deepcopy(p)
    p['story_plan']=plan
    p['plan_fingerprint']=plan_fingerprint(p)
    p['duration_plan']={**duration_plan_manifest(plan,p),'status':'ready',
                        'input_fingerprint':p['plan_fingerprint'],'geometry':geometry(plan),'schedule':copy.deepcopy(plan)}
    p['script_language']=p['title_language']=p['settings']['language']
    p['narrations']=[Narration(id='story-'+w['id'],segment_id=w['id'],start=w['start'],text=w['narration'],
                              section=w['section'],part=w['part'],evidence=w['evidence'],speech_kind='review',
                              target_duration=w['end']-w['start']-.04).model_dump()
                     for w in slots(plan) if w['narration']]
    for n in p['narrations']:
        n.update(audio='voices/test.wav',duration=n['target_duration']-.2,caption_version=4,
                 cues=[dict(id='ai',start=0,end=n['target_duration']-.2,text=n['text'],speaker='ai',words=[])])
        n['audio_hash']=providers.voice_hash(n,p['settings'])
    return p


def test_zero_is_review_toggle_and_does_not_enable_character_dubbing():
    from backend.reaction_dubbing import active as dubbed
    s=Settings(editorial_mode='reaction_cops',reaction_audio_mode='dubbed',reaction_commentary_count=0).model_dump()
    assert active(s) and not dubbed(s)
    s['reaction_commentary_count']=1
    assert not active(s) and dubbed(s)
    assert not active(Settings(reaction_commentary_count=0).model_dump())
    with pytest.raises(ValueError):Settings(reaction_commentary_count=-1)
    with pytest.raises(ValueError):Settings(reaction_commentary_count=11)


@pytest.mark.parametrize('seconds',[1,2,6,25])
def test_visual_cuts_independent_of_complete_source_words(seconds):
    p,plan=fixture(length=12,count=4)
    p['settings'].update(reaction_scene_duration_mode='range',reaction_scene_min_seconds=1,
                         reaction_scene_max_seconds=seconds)
    locked=reaction_cops.validate_plan(prepare(plan,p),p,check_text=False)
    assert sum(r['end']-r['start'] for r in locked['selections'])==pytest.approx(48)
    assert all(1-.001<=r['end']-r['start']<=seconds+.001 for r in locked['selections'])
    assert all(w['end']-w['start']<=25.001 for w in slots(locked) if w['narration'])
    assert {r['id'] for i,r in enumerate(locked['selections']) if r['narration'] for r in members(locked,i)} == {r['id'] for r in locked['selections']}
    if seconds<6:assert any(r.get('commentary_span',1)>1 for r in locked['selections'])


def test_default_visual_cuts_and_more_than_ten_review_windows():
    p,plan=fixture(length=12,count=24)
    locked=reaction_cops.validate_plan(prepare(plan,p),p,check_text=False)
    assert all(r['end']-r['start']<=6.001 for r in locked['selections'])
    assert len([s for s in slots(locked) if s['narration']])>10
    assert locked['selections'][0]['section']=='opening'
    assert any(r['section']=='ending' for r in locked['selections'])


@pytest.mark.parametrize('bad',['missing_voice','narrator','unknown','bad_citation','source_hook','hook_citation','overlap'])
def test_review_rejects_uncovered_or_unclean_plan(bad):
    p,plan=fixture()
    locked=reaction_cops.validate_plan(prepare(plan,p),p,check_text=False)
    if bad=='missing_voice':
        locked['selections'][0]['narration']='';locked['selections'][0].pop('commentary_span',None)
    elif bad in ('narrator','unknown'):p['source_speech']['items'][0].update(role='commentary' if bad=='narrator' else 'unknown')
    elif bad=='bad_citation':locked['selections'][0]['evidence']='SRC_CUES=["fake"]\nNot evidence'
    elif bad in ('source_hook','hook_citation'):
        p['settings']['hook_enabled']=True
        locked['hook'].update(start=0,end=5,narration='A dispute is being checked.',
                              original_audio=bad=='source_hook',reason='SRC_CUES=["fake"]')
    else:locked['selections'][1]['narration']='A competing voice.'
    with pytest.raises(ValueError):validate(locked,p,check_text=False)


@pytest.mark.parametrize('language',['English','Vietnamese'])
def test_all_original_audio_and_captions_off_including_hook_and_tails(tmp_path,language):
    p,plan=fixture()
    p['settings'].update(hook_enabled=True,summary_seconds=35,language=language)
    plan['hook'].update(start=0,end=5,narration='__write__',original_audio=True,reason='SRC_CUES=["c0"]\nQuestion.')
    locked=reaction_cops.validate_plan(prepare(plan,p),p,check_text=False)
    finished=write(locked,p,outline(p),writer,tmp_path,lambda *a:None,lambda:None)
    r=ready(p,finished)
    r['transcript_language']='English'
    timeline=build_story(r,strict=True)
    assert timeline['source_mutes']==[{'start':0.0,'end':35}]
    assert timeline['original_audio']==[]
    assert timeline['narration_mix']['original_ratio']==0
    assert timeline['cues'] and all(c['speaker']=='ai' for c in timeline['cues'])
    assert len(timeline['voices'])==len(r['narrations'])


def test_strict_render_rejects_short_review_instead_of_restoring_original(tmp_path):
    p,plan=fixture()
    out=write(reaction_cops.validate_plan(prepare(plan,p),p,check_text=False),p,outline(p),writer,tmp_path,lambda *a:None,lambda:None)
    r=ready(p,out);r['narrations'][0]['duration']=1
    with pytest.raises(ValueError,match='Full review'):build_story(r,strict=True)


def test_write_resume_and_targeted_semantic_retry(tmp_path):
    p,plan=fixture(length=12,count=6)
    locked=reaction_cops.validate_plan(prepare(plan,p),p,check_text=False)
    calls=[];rejected=False
    def fake(prompt,*args):
        nonlocal rejected
        ans=writer(prompt,*args);schema=args[-1];calls.append((schema,ans))
        if schema is ReviewCheck and not rejected:
            ans['items'][0].update(valid=False,issue='Unsupported conclusion');rejected=True
        return ans
    first=write(copy.deepcopy(locked),p,outline(p),fake,tmp_path,lambda *a:None,lambda:None)
    text_calls=[a for schema,a in calls if schema is ReviewText]
    assert len(text_calls[1]['items'])==1
    calls.clear()
    assert write(copy.deepcopy(locked),p,outline(p),fake,tmp_path,lambda *a:None,lambda:None)==first
    assert not calls


def test_fabricated_future_citations_and_malformed_json_have_bounded_retry(tmp_path):
    p,plan=fixture()
    locked=reaction_cops.validate_plan(prepare(plan,p),p,check_text=False)
    calls=[]
    def fake(prompt,*args):
        calls.append(prompt)
        if len(calls)==1:return {'items':[]}
        ans=writer(prompt,*args)
        for line in ans['items']:line['source_cue_ids']=['unknown_future']
        return ans
    with pytest.raises(ValueError,match='5 lượt'):write(locked,p,outline(p),fake,tmp_path,lambda *a:None,lambda:None)
    assert len(calls)==5 and len(set(calls))==5


def test_confirmed_detail_repaired_with_rejected_text_and_independent_verdict(tmp_path):
    p,plan=fixture(length=12,count=4)
    p['reaction_cues'][0]['text']='Yeah. Just some roaches.'
    locked=reaction_cops.validate_plan(prepare(plan,p),p,check_text=False)
    checked=0
    corrected='She confirms the presence of roaches. The other possibility remains unconfirmed, so those two details cannot be treated alike.'
    def fake(prompt,*args):
        nonlocal checked
        answer=writer(prompt,*args)
        if args[-1] is ReviewCheck:
            checked+=1
            if checked<=3:
                answer['items'][0].update(valid=False,issue='Roaches were explicitly confirmed; only the other possibility is uncertain.')
        elif args[-1] is ReviewText and checked>=3:
            assert 'REJECTED TEXT' in prompt and 'explicitly confirmed' in prompt
            assert 'Conservative evidence repair' in prompt
            answer['items'][0]['text']=corrected
        return answer
    result=write(locked,p,outline(p),fake,tmp_path,lambda *a:None,lambda:None)
    assert checked==4
    assert result['selections'][0]['narration']==corrected


def test_persistent_factual_rejection_never_becomes_unverified_fallback(tmp_path):
    p,plan=fixture()
    locked=reaction_cops.validate_plan(prepare(plan,p),p,check_text=False)
    def fake(prompt,*args):
        answer=writer(prompt,*args)
        if args[-1] is ReviewCheck:
            for item in answer['items']:
                item.update(valid=False,issue='Unsupported factual claim')
        return answer
    with pytest.raises(ValueError,match='5 lượt'):
        write(locked,p,outline(p),fake,tmp_path,lambda *a:None,lambda:None)
    checkpoint=json.loads(next((tmp_path/'reaction-review-cache').glob('*.json')).read_text('utf-8'))
    assert checkpoint['accepted']=={}
    assert checkpoint['failures'][0]['feedback']


def test_short_target_can_cut_inside_a_long_source_utterance(tmp_path):
    p,plan=fixture(length=16,count=3);p['settings']['summary_seconds']=12
    out,hook,budget,events,count=build(p,writer,tmp_path,lambda *a:None,lambda:None)
    assert sum(r['end']-r['start'] for r in out['selections'])==pytest.approx(12)
    validate(out,p)


def test_full_plan_orchestration_manifest_and_voice_kind(tmp_path,monkeypatch):
    p,_=fixture()
    monkeypatch.setattr(store,'project_dir',lambda _:tmp_path)
    monkeypatch.setattr(reaction_cops,'optimize_cues',lambda *a,**kw:{'cues':p['reaction_cues']})
    monkeypatch.setattr(source_speech,'classify',lambda *a,**kw:p['source_speech'])
    monkeypatch.setattr(providers,'ask_ai',writer)
    result=reaction_cops.plan_reaction(p,lambda *a:None,lambda:None)
    assert result['duration_plan']['narration_profile']=='full_review'
    assert result['duration_plan']['review_outline']['chapters']
    assert all(n['speech_kind']=='review' for n in result['narrations'])
    assert result['duration_plan']['retention']['actual_ratio']==0
    assert result['settings']['reaction_commentary_count']==0
    contract_check(result)


def test_full_review_fit_uses_exact_selected_speed_and_repairs_short_or_long(tmp_path,monkeypatch):
    raw=tmp_path/'raw.wav';raw.write_bytes(b'raw');out=tmp_path/'voice.wav'
    monkeypatch.setattr(providers,'probe',lambda p:{'duration':11.8 if Path(p)==raw else 9.44})
    filters=[]
    def fake_run(args,check_cancel):
        filters.append(args[args.index('-af')+1]);Path(args[-1]).write_bytes(b'voice')
    monkeypatch.setattr(media,'run',fake_run)
    gap=reaction_tail_gap({'editorial_mode':'reaction_cops','reaction_commentary_count':0,'production_workflow':'plan_first'},9.7)
    fit=providers.fit_voice_audio(raw,out,lambda:None,9.7,speed=1.25,stable_speed=1.25,tail_gap=gap,exact_speed=True)
    assert filters==['atempo=1.25000000'] and fit['tempo']==1.25
    for seconds in (5,12.3):
        monkeypatch.setattr(providers,'probe',lambda _,t=seconds:{'duration':t})
        with pytest.raises(DurationMismatchError):
            providers.fit_voice_audio(raw,out,lambda:None,9.7,stable_speed=1.25,tail_gap=gap,exact_speed=True)


def test_review_quality_not_limited_to_two_sentences_but_checks_suffix():
    text='The first response leaves a question open. The denial challenges the claim. The check is therefore still relevant.'
    assert quality_issue(text) is None
    assert quality_issue(text+' SRC_CUES=["c1"]')
    assert quality_issue('I did not take the money. I did not take the money.', 'I did not take the money.')


def test_muted_source_never_calls_source_alignment_or_translation(tmp_path,monkeypatch):
    from backend.source_captions import refresh_source
    p,_=fixture();p['script_language']=p['title_language']='English'
    stats,missing=refresh_source(p,lambda *a:None,lambda:None,lambda *a:pytest.fail('source ASR'),lambda *a:None)
    assert not missing and stats['asr_calls']==0
    monkeypatch.setattr(store,'save',lambda p:p)
    monkeypatch.setattr(providers,'_translate_batch',lambda *a:pytest.fail('source translation'))
    providers.localize(p,lambda *a:None,lambda:None)


def test_measured_repair_is_reviewed_before_acceptance(tmp_path,monkeypatch):
    from backend import voice_repair
    p,plan=fixture();p['story_plan']=plan
    n=dict(text='A response disputes the claim.',evidence=plan['selections'][0]['evidence'])
    monkeypatch.setattr(voice_repair,'repair_text',lambda *a,**kw:'The first statement raises a question. Further checking is needed to establish what happened.')
    calls=[]
    def judge(prompt,*args):
        calls.append(prompt)
        return {'items':[dict(id='repair',valid=len(calls)>1,issue='Check attribution')]}
    out=repair_voice(p,n,10,8,judge,tmp_path,lambda:None,1,[])
    assert len(calls)==2 and 'Further checking' in out


def test_review_does_not_reassign_locked_scenes():
    from backend.scene_duration_repair import adjust
    p,plan=fixture();p['story_plan']=plan
    assert adjust(p,{'segment_id':'sel0'},5,10) is None


def test_outline_allows_context_overlap_that_previously_exhausted_retries():
    p,_=fixture()
    raw=outline(p)
    raw['chapters']=[dict(focus='A question and a check.',source_cue_ids=['c0','c2']),
                     dict(focus='The answer disputes the question.',source_cue_ids=['c1','c2'])]
    result,audit=normalize_outline(raw,p['reaction_cues'])
    assert result==raw and audit['context_overlaps']==[{'previous':1,'next':2}]


def test_outline_normalizes_chapters_and_reference_lists_without_rewriting_facts():
    p,_=fixture();raw=outline(p)
    raw['chapters']=[dict(focus='A late check.',source_cue_ids=['c2']),
                     dict(focus='A question is disputed.',source_cue_ids=['c1','c0','c1'])]
    result,audit=normalize_outline(raw,p['reaction_cues'])
    assert result['chapters']==[dict(focus='A question is disputed.',source_cue_ids=['c0','c1']),
                               dict(focus='A late check.',source_cue_ids=['c2'])]
    assert len(audit['normalizations'])==2
    assert raw['chapters'][0]['source_cue_ids']==['c2']  # input unmodified


def test_outline_does_not_silently_drop_unknown_or_excluded_evidence():
    p,_=fixture();raw=outline(p)
    raw['chapters'][0]['source_cue_ids'].append('unknown')
    with pytest.raises(ValueError,match='unknown/non-admissible'):normalize_outline(raw,p['reaction_cues'])
    raw=outline(p)
    with pytest.raises(ValueError,match='c1'):normalize_outline(raw,[p['reaction_cues'][0],p['reaction_cues'][2]])


def test_broad_chapter_focus_cannot_leak_future_conclusion_to_earlier_voice():
    p,_=fixture();raw=outline(p);known={c['id']:c for c in p['reaction_cues']}
    raw['chapters']=[dict(focus='A current dispute.',source_cue_ids=['c0']),
                     dict(focus='A later check changes the stakes.',source_cue_ids=['c0','c2'])]
    assert visible_focus(raw,{'c0'},known)==['A current dispute.']
    assert visible_focus(raw,{'c2'},known)==['A later check changes the stakes.']


def test_outline_overlap_builds_once_and_logs_normalization(tmp_path):
    p,_=fixture();calls=[]
    def fake(prompt,*args):
        calls.append(args[-1])
        if args[-1] is ReviewOutline:
            raw=outline(p)
            raw['chapters']=[dict(focus='A question and a check.',source_cue_ids=['c0','c2']),
                             dict(focus='A disputed answer.',source_cue_ids=['c1','c2'])]
            return raw
        return writer(prompt,*args)
    out,*_=build(p,fake,tmp_path,lambda *a:None,lambda:None)
    assert calls.count(ReviewOutline)==1
    assert all(a['end']<=b['start']+.001 for a,b in zip(out['selections'],out['selections'][1:]))
    audit=json.loads((tmp_path/p['reaction_review_outline_audit']).read_text('utf-8'))
    assert audit['status']=='accepted' and audit['attempts'][0]['context_overlaps']


def test_outline_invalid_citations_get_actionable_logged_bounded_retry(tmp_path):
    p,_=fixture();calls=[]
    def bad(prompt,*args):
        calls.append(prompt);raw=outline(p);raw['chapters'][0]['source_cue_ids']=['wrong_cue'];return raw
    with pytest.raises(ValueError,match='sau 3 lượt') as exc:
        build(p,bad,tmp_path,lambda *a:None,lambda:None)
    assert 'ATTEMPT: 4' not in str(exc.value) and 'wrong_cue' in str(exc.value)
    audit=json.loads(next((tmp_path/'reaction-review-outline').glob('*.json')).read_text('utf-8'))
    assert len(calls)==len(audit['attempts'])==3
    assert audit['status']=='rejected' and all(a['raw'] for a in audit['attempts'])


def test_review_final_chapter_validation_uses_same_catalog_as_selection(monkeypatch):
    p,plan=fixture(length=12,count=12)
    p['settings'].update(reaction_scene_duration_mode='range',reaction_scene_min_seconds=1,
                         reaction_scene_max_seconds=3)
    original=reaction_cops._clean_footage
    def alternative_grouping(project,*args,**kwargs):
        if project['settings'].get('reaction_scene_duration_mode')=='range':
            # A display/cue grouping may find a later source boundary than the
            # scheduler's continuous clean blocks. It must not redefine the
            # final development of the already selected review footage.
            return [{'start':145,'end':146,'source_cue_ids':['c11'],'priority':.8}]
        return original(project,*args,**kwargs)
    monkeypatch.setattr(reaction_cops,'_clean_footage',alternative_grouping)
    locked=reaction_cops.validate_plan(prepare(plan,p),p,check_text=False)
    assert locked['selections'][-1]['end']==144


def test_excel_import_keeps_zero_override(tmp_path):
    from openpyxl import Workbook
    from backend.batches import parse_excel, effective_settings
    workbook=Workbook();sheet=workbook.active
    sheet.append(['url','editorial_mode','reaction_commentary_count'])
    sheet.append(['https://www.youtube.com/watch?v=EXiQCyqxmSE','reaction_cops',0])
    path=tmp_path/'batch.xlsx';workbook.save(path)
    # Import API uses bytes to avoid trusting paths from uploaded spreadsheets.
    rows=parse_excel(path.read_bytes())
    assert rows[0]['settings']['reaction_commentary_count']==0
    assert effective_settings(Settings().model_dump(),rows[0]['settings'])['reaction_commentary_count']==0


def test_synthesis_rewrites_review_without_changing_voice_pace_or_geometry(tmp_path,monkeypatch):
    from backend import vieneu, reaction_review
    p,plan=fixture()
    out=write(reaction_cops.validate_plan(prepare(plan,p),p,check_text=False),p,outline(p),writer,tmp_path,lambda *a:None,lambda:None)
    p=ready(p,out);n=p['narrations'][0];n.update(audio='',audio_hash='',duration=0)
    original_geometry=geometry(out)
    monkeypatch.setattr(store,'project_dir',lambda _:tmp_path)
    monkeypatch.setattr(store,'save',lambda p:p)
    monkeypatch.setattr(providers,'probe',lambda path:{'duration':float(Path(path).read_text())})
    monkeypatch.setattr(reaction_cops,'repair_commentary_for_voice',lambda *a:pytest.fail('Sparse commentary rules used for review'))
    calls=[]
    def generate(settings,text,path,*args,**kw):
        calls.append((settings['voice_speed'],settings['vieneu_voice'],text))
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(str(20 if len(calls)==1 else n['target_duration']-.2))
        return path
    def fit(raw,dest,*args,**kw):
        assert kw['exact_speed'] is True
        measured=float(raw.read_text())
        if measured>n['target_duration']:
            raise DurationMismatchError('VieNeu',measured,n['target_duration'],audio_path=raw)
        dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(str(measured));return {'tempo':1}
    monkeypatch.setattr(vieneu,'generate',generate)
    monkeypatch.setattr(providers,'fit_voice_audio',fit)
    monkeypatch.setattr(reaction_review,'repair_voice',lambda *a,**kw:'The response contests the claim. The question remains unresolved. Checking is needed before drawing a conclusion.')
    monkeypatch.setattr(providers,'transcribe',lambda path,settings,check,expected_text:[dict(id='ai',start=0,end=n['target_duration']-.2,text=expected_text,words=[])])
    result=providers.synthesize(p,lambda *a:None,lambda:None,only_id=n['id'])
    assert len(calls)==2 and calls[0][:2]==calls[1][:2]
    assert geometry(result['story_plan'])==original_geometry
    assert result['narrations'][0]['duration']==pytest.approx(n['target_duration']-.2)
    assert result['narrations'][0]['text']==result['duration_plan']['schedule']['selections'][0]['narration']
    contract_check(result)


def test_native_review_render_has_only_ai_tone_even_in_pauses(tmp_path,monkeypatch):
    import shutil
    from backend.media import FFMPEG,run,probe
    if not shutil.which(FFMPEG):pytest.skip('FFmpeg required')
    import av
    import numpy as np
    from backend.render import render_part
    monkeypatch.setattr(store,'DATA',tmp_path)
    monkeypatch.setattr(store,'DB',tmp_path/'studio.sqlite3')
    store.init()
    base=store.create('Full review audio check',{'kind':'upload','file':'source.mp4'})
    folder=store.project_dir(base['id'])
    run([FFMPEG,'-y','-f','lavfi','-i','color=blue:s=160x120:r=30:d=12',
         '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=12',
         '-c:v','libx264','-c:a','aac',folder/'source.mp4'])
    run([FFMPEG,'-y','-f','lavfi','-i','sine=frequency=880:sample_rate=48000:duration=11.7',folder/'voice.wav'])
    p,plan=fixture(length=4)
    p.update(id=base['id'],metadata=probe(folder/'source.mp4'))
    p['settings'].update(render_encoder='cpu',subtitles=False,title='',show_title=False,duck_volume=1,original_volume=1)
    out=write(reaction_cops.validate_plan(prepare(plan,p),p,check_text=False),p,outline(p),writer,folder,lambda *a:None,lambda:None)
    r=ready(p,out)
    assert len(r['narrations'])==1
    n=r['narrations'][0]
    n.update(audio='voice.wav',duration=11.7,cues=[],caption_version=4,audio_hash=providers.voice_hash(n,r['settings']))
    timeline=build_story(r,strict=True)
    result=render_part(r,timeline,timeline['parts'][0],folder,lambda:None,width=360)
    assert probe(store.asset(r['id'],result['file']))['duration']==pytest.approx(12,abs=.1)
    with av.open(str(store.asset(r['id'],result['file']))) as video:
        samples=np.concatenate([f.to_ndarray().mean(axis=0) for f in video.decode(audio=0)])
        rate=video.streams.audio[0].rate
    x=samples[int(.05*rate):int(.5*rate)];t=np.arange(len(x))/rate
    level=lambda freq:abs(np.mean(x*np.exp(-2j*np.pi*freq*t)))
    assert level(880)>level(440)*30
    tail=samples[int(11.8*rate):int(11.95*rate)]
    assert np.sqrt(np.mean(tail*tail))<.001
