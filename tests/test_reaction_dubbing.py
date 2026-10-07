import copy
import json
import shutil

import pytest

from backend import providers, store
from backend.models import Narration, Settings
from backend.reaction_cops import validate_plan
from backend.reaction_dubbing import (DubAnswer, DubReview, active, drafts, write, validate,
                                      repair, apply_turns, captions)
from backend.hook_policy import slots
from backend.plan_first import geometry, contract_check
from backend.story import plan_fingerprint, duration_plan_manifest
from backend.timeline import build_story
from backend.voice_repair import reaction_tail_gap, restore_perspective


def fixture(length=10):
    settings = Settings(editorial_mode='reaction_cops', reaction_audio_mode='dubbed',
                        reaction_commentary_count=2, output_mode='single', summary_seconds=length*3,
                        narration_style='storytelling',production_workflow='plan_first',language='English').model_dump()
    cues = [dict(id=f'c{i}', start=i*length, end=(i+1)*length, text=t, words=[], speaker='original')
            for i,t in enumerate(['I have a question.', 'I did not take the money.', 'We still need to check.'])]
    roles = [dict(cue=i,start=c['start'],end=c['end'],text=c['text'],role='participant',confidence=.9,
                  priority=.8,evidence='In-scene statement',hook_score=0) for i,c in enumerate(cues)]
    project = dict(id='dub-test',settings=settings,metadata={'duration':length*3,'has_audio':True},
                   source={'kind':'upload','file':'source.mp4'},reaction_cues=cues,source_speech={'version':3,'items':roles},
                   transcript=cues,narrations=[],warnings=[],exports=[],preview_exports=[])
    selections = [dict(start=c['start'],end=c['end'],part=1,section='development',
                       narration='' if i == 1 else 'This answer leaves the central question unresolved.',
                       narration_offset=0,evidence='SRC_CUES='+json.dumps([c['id']])+'\n'+c['text'],
                       priority=.8,reason='confirmed development') for i,c in enumerate(cues)]
    plan = dict(title='A Different Situation',synopsis='Confirmed exchange',outcome='Check continues',
                lesson='No spoken lesson',hook=dict(start=0,end=0,title='',reason='off',original_audio=False,narration=''),
                selections=selections)
    return project, validate_plan(plan,project,check_text=False)


def translator(prompt, images, settings, folder, check, schema):
    if schema is DubReview:
        entries=json.loads(prompt.split('REQUESTED: ')[1])
        return {'items':[{'id':e['id'],'valid':True,'issue':''} for e in entries]}
    entries=json.loads(prompt.split('REQUESTED: ')[1].split('\nGENERATION:')[0])
    return {'items':[{'id':e['id'],'turns':[dict(source_cue_ids=[c['id']],speaker_label='Person speaking',text=c['text'])
                                          for c in e['source_cues']]} for e in entries]}


def ready(project,plan):
    project=copy.deepcopy(project)
    project['story_plan']=plan
    project['plan_fingerprint']=plan_fingerprint(project)
    project['duration_plan']={**duration_plan_manifest(plan,project),'status':'ready',
                              'input_fingerprint':project['plan_fingerprint'],'geometry':geometry(plan),'schedule':copy.deepcopy(plan)}
    project['script_language']=project['settings']['language']
    project['narrations']=[dict(id='story-'+w['id'],segment_id=w['id'],start=w['start'],text=w['narration'],
                               evidence=w['evidence'],part=w['part'],section=w['section'],enabled=True,
                               target_duration=w['end']-w['start']-.04,
                               **({k:copy.deepcopy(w[k]) for k in ('speech_kind','turns','selection_ids','dialogue_window_id','dialogue_turn_index','output_offset','output_end')}
                                  if w.get('speech_kind')=='dialogue' else {}))
                           for w in slots(plan) if w['narration']]
    return project


def test_dubs_do_not_count_as_commentary_and_preserve_first_person(tmp_path):
    p,plan=fixture()
    out=write(plan,p,translator,tmp_path,lambda *a:None,lambda:None)
    out=validate_plan(out,p,check_text=False)
    validate(out,p)
    assert sum(bool(s['narration']) for s in out['selections'])==2
    assert len(out['dialogue_dubs'])==1
    assert out['dialogue_dubs'][0]['narration']=='I did not take the money.'
    n=ready(p,out)['narrations'][1]
    assert Narration.model_validate(n).speech_kind=='dialogue'
    assert reaction_tail_gap(p['settings'],9.96)==9.96
    contract_check(ready(p,out))


def test_resume_uses_accepted_translation_and_retries_only_failed_windows(tmp_path):
    p,plan=fixture()
    calls=[]
    def fake(*args):
        calls.append(args[-1]);return translator(*args)
    first=write(plan,p,fake,tmp_path,lambda *a:None,lambda:None)
    assert calls==[DubAnswer,DubReview]
    calls.clear()
    assert write(plan,p,fake,tmp_path,lambda *a:None,lambda:None)==first
    assert not calls
    p['settings']['language']='Vietnamese'
    write(plan,p,fake,tmp_path,lambda *a:None,lambda:None)
    assert calls  # language change invalidates the translation checkpoint


def test_reviewer_provider_failure_saves_candidates_and_resumes_review_only(tmp_path):
    from backend.ai_errors import AIProviderError
    p, plan = fixture()
    def fail_review(*args):
        if args[-1] is DubReview:
            raise AIProviderError('Codex', 'invalid_json_schema', 'Missing issue')
        return translator(*args)
    with pytest.raises(AIProviderError):
        write(plan, p, fail_review, tmp_path, lambda *a: None, lambda: None)
    path = next((tmp_path / 'dialogue-plans').glob('*.json'))
    checkpoint = json.loads(path.read_text('utf-8'))
    assert not checkpoint['accepted'] and set(checkpoint['pending_review']) == {'dub0'}
    calls = []
    def resume(*args):
        calls.append(args[-1])
        assert args[-1] is DubReview
        return translator(*args)
    result = write(plan, p, resume, tmp_path, lambda *a: None, lambda: None)
    assert calls == [DubReview]
    validate(result, p)
    checkpoint = json.loads(path.read_text('utf-8'))
    assert checkpoint['generation'] == 1
    assert not checkpoint['pending_review'] and set(checkpoint['accepted']) == {'dub0'}


def test_missing_review_field_never_accepts_unverified_dialogue(tmp_path):
    p, plan = fixture()
    calls = []
    def missing_issue(*args):
        calls.append(args[-1])
        answer = translator(*args)
        if args[-1] is DubReview:
            del answer['items'][0]['issue']
        return answer
    with pytest.raises(ValueError, match='Không dịch đủ'):
        write(plan, p, missing_issue, tmp_path, lambda *a: None, lambda: None)
    assert calls == [DubAnswer, DubReview, DubReview, DubReview]
    checkpoint = json.loads(next((tmp_path / 'dialogue-plans').glob('*.json')).read_text('utf-8'))
    assert not checkpoint['accepted'] and checkpoint['pending_review']


def test_rejected_review_only_regenerates_failed_ids_in_batch(tmp_path):
    p, plan = fixture()
    for row in plan['selections']:
        row['narration'] = ''
    reviews, translated_ids = 0, []
    def review_once(*args):
        nonlocal reviews
        answer = translator(*args)
        if args[-1] is DubReview:
            reviews += 1
            if reviews == 1:
                answer['items'][-1].update(valid=False, issue='Missing negation')
        else:
            translated_ids.append([i['id'] for i in answer['items']])
        return answer
    write(plan, p, review_once, tmp_path, lambda *a: None, lambda: None)
    assert translated_ids == [['dub0', 'dub1'], ['dub1']]


def test_cancellation_before_review_retains_pending_translation(tmp_path):
    from backend.media import Cancelled
    p, plan = fixture()
    cancel = False
    def translate(*args):
        nonlocal cancel
        answer = translator(*args)
        cancel = True
        return answer
    def check():
        if cancel:
            raise Cancelled('Stop')
    with pytest.raises(Cancelled):
        write(plan, p, translate, tmp_path, lambda *a: None, check)
    checkpoint = json.loads(next((tmp_path / 'dialogue-plans').glob('*.json')).read_text('utf-8'))
    assert checkpoint['pending_review'] and not checkpoint['accepted']
    write(plan, p, translator, tmp_path, lambda *a: None, lambda: None)


def test_legacy_accepted_checkpoint_remains_usable(tmp_path):
    p, plan = fixture()
    write(plan, p, translator, tmp_path, lambda *a: None, lambda: None)
    path = next((tmp_path / 'dialogue-plans').glob('*.json'))
    checkpoint = json.loads(path.read_text('utf-8'))
    path.write_text(json.dumps({k: checkpoint[k] for k in ('accepted', 'generation')}), 'utf-8')
    write(plan, p, lambda *a: pytest.fail('Old accepted checkpoint must resume'),
          tmp_path, lambda *a: None, lambda: None)


@pytest.mark.parametrize('problem',['missing','duplicate','narrator','wrong_order'])
def test_turn_coverage_and_role_reject_invalid_dialogue(tmp_path,problem):
    p,plan=fixture()
    out=write(plan,p,translator,tmp_path,lambda *a:None,lambda:None)
    w=out['dialogue_dubs'][0]
    if problem=='missing':w['turns'][0]['source_cue_ids']=[]
    elif problem=='duplicate':w['turns'][0]['source_cue_ids']*=2
    elif problem=='narrator':p['source_speech']['items'][1]['role']='commentary'
    else:w['turns'][0]['source_cue_ids']=['c2','c1']
    with pytest.raises(ValueError):validate(out,p)


def test_semantic_review_rejects_negation_change_and_retries(tmp_path):
    p,plan=fixture()
    reviews=0
    requests=[]
    def fake(prompt,*args):
        nonlocal reviews
        schema=args[-1]
        answer=translator(prompt,*args)
        if schema is DubReview:
            reviews+=1
            if reviews==1:answer['items'][0].update(valid=False,issue='Negation removed')
        else:requests.append(prompt)
        return answer
    out=write(plan,p,fake,tmp_path,lambda *a:None,lambda:None)
    assert reviews==2 and 'Negation removed' in requests[1]
    validate(out,p)


def test_all_dialogue_in_retained_scene_is_dubbed_not_only_planner_citations(tmp_path):
    p,plan=fixture()
    p['reaction_cues'][1]['end']=15
    p['source_speech']['items'][1]['end']=15
    cue=dict(id='extra',start=15,end=20,text='Please check the receipt.',speaker='original',words=[])
    role=dict(cue=3,start=15,end=20,text=cue['text'],role='participant',confidence=.9,priority=.8,
              evidence='In-scene response',hook_score=0)
    p['reaction_cues'].insert(2,cue)
    p['source_speech']['items'].insert(2,role)
    out=write(plan,p,translator,tmp_path,lambda *a:None,lambda:None)
    assert [i for t in out['dialogue_dubs'][0]['turns'] for i in t['source_cue_ids']]==['c1','extra']
    assert [s['start'] for s in slots(out) if s.get('speech_kind')=='dialogue']==[10,15]
    validate(out,p)


def test_multi_cut_dialogue_window_and_part_boundary(tmp_path):
    p,plan=fixture(length=6)
    # Two dialogue scenes after the only commentary.
    p['settings']['reaction_commentary_count']=1
    plan['selections'][2]['narration']=''
    out=write(plan,p,translator,tmp_path,lambda *a:None,lambda:None)
    w=out['dialogue_dubs'][0]
    assert w['selection_ids']==['sel1','sel2'] and w['end']-w['start']==12
    validate(out,p)
    out['selections'][2]['part']=2
    with pytest.raises(ValueError,match='Thời lượng'):validate(out,p)


def test_hook_dubbing_keeps_selected_highlight_without_original_audio(tmp_path):
    p,plan=fixture(length=5)
    p['settings'].update(hook_enabled=True,summary_seconds=20)
    plan['hook'].update(start=5,end=10,original_audio=True,reason='verified highlight')
    out=write(plan,p,translator,tmp_path,lambda *a:None,lambda:None)
    assert slots(out)[0]['id']=='hookt0'
    assert not out['hook']['original_audio']
    validate_plan(out,p,check_text=False)
    out['hook_dub']['turns'][0]['source_cue_ids']=['c0']
    with pytest.raises(ValueError):validate(out,p)


def test_locked_contract_rejects_missing_dubbing_and_manual_turn_edits(tmp_path):
    p,plan=fixture()
    with pytest.raises(ValueError,match='Chưa lồng đủ'):contract_check(ready(p,plan))
    out=write(plan,p,translator,tmp_path,lambda *a:None,lambda:None)
    r=ready(p,out)
    r['narrations'][1]['turns'][0]['speaker_label']='Different label'
    with pytest.raises(ValueError,match='slot'):contract_check(r)


def test_dialogue_geometry_detects_reassigned_windows(tmp_path):
    p,plan=fixture()
    out=write(plan,p,translator,tmp_path,lambda *a:None,lambda:None)
    original=geometry(out)
    out['dialogue_dubs'][0]['selection_ids']=['sel2']
    assert geometry(out)!=original


def test_dialogue_duration_repair_keeps_turns_and_source_meaning(tmp_path):
    p,plan=fixture()
    out=write(plan,p,translator,tmp_path,lambda *a:None,lambda:None)
    r=ready(p,out)
    n=next(n for n in r['narrations'] if n.get('speech_kind')=='dialogue')
    def fake(prompt,images,settings,folder,check,schema):
        if schema is DubReview:return {'items':[dict(id='dub0t0',valid=True,issue='')]}
        return {'items':[dict(id='dub0t0',turns=[dict(source_cue_ids=['c1'],speaker_label='Person speaking',text="I didn't take it.")])]}
    text,turns=repair(r,n,9.96,12,fake,tmp_path,lambda:None,1)
    apply_turns(r,n,turns)
    n['text']=text
    assert r['duration_plan']['schedule']['dialogue_dubs'][0]['narration']==text
    contract_check(r)
    r['voice_repair_state']={n['id']:{'approved_text':'No money was taken.','history':[{'text':text}]}}
    assert not restore_perspective(r)


def test_repair_cannot_change_speaker_or_turn_mapping(tmp_path):
    p,plan=fixture();r=ready(p,write(plan,p,translator,tmp_path,lambda *a:None,lambda:None))
    n=r['narrations'][1]
    def fake(*args):
        return {'items':[dict(id='dub0t0',turns=[dict(source_cue_ids=['c1'],speaker_label='Police chief',text='No.')])]}
    with pytest.raises(ValueError,match='Chưa rút gọn'):repair(r,n,9.96,12,fake,tmp_path,lambda:None,1)


def test_caption_split_preserves_turns_and_does_not_highlight_character_words():
    turns=[dict(source_cue_ids=['1'],speaker_label='Person A',text='Where is it?'),
           dict(source_cue_ids=['2'],speaker_label='Person B',text='I do not know.')]
    n={'turns':turns,'text':' '.join(t['text'] for t in turns)}
    words=[dict(text=t,start=i*.2,end=(i+1)*.2) for i,t in enumerate(n['text'].split())]
    result,aligned=captions(n,[dict(words=words)],2)
    assert aligned and len(result)==2
    assert result[0]['text']=='Person A: Where is it?'
    assert result[1]['text']=='Person B: I do not know.'
    assert all(not c['words'] for c in result)
    result,aligned=captions(n,[dict(text=n['text'])],2)
    assert not aligned and len(result)==1 and 'Person B:' in result[0]['text']


def test_full_source_mute_includes_pauses_and_no_source_captions(tmp_path):
    p,plan=fixture(length=6)
    plan['selections'][2]['narration']=''
    out=write(plan,p,translator,tmp_path,lambda *a:None,lambda:None)
    r=ready(p,out)
    for n in r['narrations']:
        n.update(audio='fake.wav',duration=2,cues=[dict(id='ai',start=0,end=2,text=n['text'],words=[])],
                 caption_version=4,audio_hash=providers.voice_hash(n,r['settings']))
    timeline=build_story(r,strict=True)
    assert timeline['original_audio']==[]
    assert timeline['source_mutes']==[{'start':0,'end':18}]
    assert all(c['speaker']=='ai' for c in timeline['cues'])
    assert timeline['voices'][1]['start']==6
    assert timeline['voices'][2]['start']==12  # The second character turn cannot drift into the previous scene.
    r['narrations'][1]['duration']=13
    with pytest.raises(ValueError,match='dài hơn'):build_story(r,strict=True)


def test_old_project_audio_choice_and_standard_mode_are_unchanged():
    assert Settings().reaction_audio_mode=='original'
    assert not active({'editorial_mode':'standard','reaction_audio_mode':'dubbed'})
    p,plan=fixture()
    p['settings']['reaction_audio_mode']='original'
    assert len(slots(plan))==3 and 'dialogue_windows' not in geometry(plan)


def test_dubbing_does_not_translate_unused_source_captions(monkeypatch,tmp_path):
    p,plan=fixture()
    p.update(script_language='English',title_language='English',transcript_language='Vietnamese',hooks=[])
    monkeypatch.setattr(store,'project_dir',lambda _:tmp_path)
    monkeypatch.setattr(store,'save',lambda p:p)
    monkeypatch.setattr(providers,'ask_ai',lambda *a:pytest.fail('Should not translate unused source captions'))
    original=copy.deepcopy(p['transcript'])
    assert providers.localize(p,lambda *a:None,lambda:None)['transcript']==original


def test_plan_reaction_integrates_both_tracks_and_ready_contract(tmp_path,monkeypatch):
    from backend.reaction_cops import plan_reaction
    from backend import reaction_cops,source_speech
    p,plan=fixture()
    p.update(summary='',scenes=[],hooks=[],source_transcript=p['transcript'])
    monkeypatch.setattr(store,'project_dir',lambda _:tmp_path)
    monkeypatch.setattr(reaction_cops,'optimize_cues',lambda *a:{'cues':p['reaction_cues']})
    monkeypatch.setattr(source_speech,'classify',lambda *a:{**p['source_speech'],'fingerprint':'verified-test'})
    text=('This question brings the disagreement into the open, giving the participants a specific issue to answer. '
          'The response needs to be checked against what is actually established, rather than treated as a conclusion.')
    def fake(prompt,images,settings,folder,check,schema):
        if schema is DubAnswer or schema is DubReview:return translator(prompt,images,settings,folder,check,schema)
        if schema.__name__=='ReactionFootagePlan':
            return dict(title='A Different Situation',synopsis='Confirmed exchange',last_confirmed_event='A check is requested',
                        selections=[dict(start=i*10,end=(i+1)*10,part=1,section='development',reason='confirmed exchange',
                                         priority=.8,source_cue_ids=[f'c{i}'],keep_original=i==1) for i in range(3)])
        if schema.__name__=='ScheduledNarration':
            rows=json.loads(prompt.split('\nREQUESTED SLOTS: ',1)[1].split('\nACCEPTED',1)[0])
            return {'items':[dict(id=r['id'],text=text) for r in rows]}
        pytest.fail(schema.__name__)
    monkeypatch.setattr(providers,'ask_ai',fake)
    result=plan_reaction(p,lambda *a:None,lambda:None)
    assert result['duration_plan']['status']=='ready'
    assert result['duration_plan']['reaction_budget']['actual_commentary_count']==2
    assert len([n for n in result['narrations'] if n.get('speech_kind')=='dialogue'])==1
    contract_check(result)


def test_synthesize_repairs_dialogue_without_commentary_audit_or_voice_change(tmp_path,monkeypatch):
    from backend import vieneu,reaction_cops
    from backend.voice_repair import DurationMismatchError
    p,plan=fixture();r=ready(p,write(plan,p,translator,tmp_path,lambda *a:None,lambda:None))
    n=next(n for n in r['narrations'] if n.get('speech_kind')=='dialogue')
    monkeypatch.setattr(store,'project_dir',lambda _:tmp_path)
    monkeypatch.setattr(store,'save',lambda p:p)
    monkeypatch.setattr(providers,'probe',lambda path:{'duration':float(path.read_text())})
    monkeypatch.setattr(reaction_cops,'repair_commentary_for_voice',lambda *a:pytest.fail('Dialogue passed through commentary audit'))
    voice_calls=[]
    def generate(settings,text,path,*args,**kw):
        voice_calls.append((settings['voice_speed'],settings['vieneu_voice'],text))
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('13' if text=='I did not take the money.' else '2')
        return path
    def fit(raw,dest,*args,**kw):
        measured=float(raw.read_text())
        if measured>kw.get('target_duration',args[1] if len(args)>1 else 0):
            raise DurationMismatchError('VieNeu',measured,n['target_duration'],audio_path=raw)
        dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(str(measured));return {'stable_speed':kw['stable_speed']}
    def ai(prompt,images,settings,folder,check,schema):
        if schema is DubReview:return {'items':[dict(id='dub0t0',valid=True,issue='')]}
        return {'items':[dict(id='dub0t0',turns=[dict(source_cue_ids=['c1'],speaker_label='Person speaking',text="I didn't take it.")])]}
    def transcribe(path,settings,check,expected_text):
        tokens=expected_text.split();words=[dict(text=t,start=i*2/len(tokens),end=(i+1)*2/len(tokens)) for i,t in enumerate(tokens)]
        return [dict(id='ai',start=0,end=2,text=expected_text,words=words)]
    monkeypatch.setattr(vieneu,'generate',generate)
    monkeypatch.setattr(providers,'fit_voice_audio',fit)
    monkeypatch.setattr(providers,'ask_ai',ai)
    monkeypatch.setattr(providers,'transcribe',transcribe)
    result=providers.synthesize(r,lambda *a:None,lambda:None,only_id=n['id'])
    assert len(voice_calls)==2 and voice_calls[0][:2]==voice_calls[1][:2]
    dubbed=next(n for n in result['narrations'] if n.get('speech_kind')=='dialogue')
    assert dubbed['duration']==2 and dubbed['cues'][0]['text'].startswith('Person speaking:')
    assert not dubbed['cues'][0]['words']
    contract_check(result)


def test_real_render_mutes_source_tone_during_voice_and_silence(tmp_path,monkeypatch):
    from backend.media import FFMPEG,run,probe
    if not shutil.which(FFMPEG):pytest.skip('FFmpeg required')
    import av
    import numpy as np
    from backend.render import render_part
    monkeypatch.setattr(store,'DATA',tmp_path)
    monkeypatch.setattr(store,'DB',tmp_path/'studio.sqlite3')
    store.init()
    base=store.create('Dubbing audio check',{'kind':'upload','file':'source.mp4'})
    folder=store.project_dir(base['id'])
    run([FFMPEG,'-y','-f','lavfi','-i','color=blue:s=160x120:r=30:d=12',
         '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=12',
         '-c:v','libx264','-c:a','aac',folder/'source.mp4'])
    run([FFMPEG,'-y','-f','lavfi','-i','sine=frequency=880:sample_rate=48000:duration=0.5',folder/'voice.wav'])
    p,plan=fixture(length=4)
    p.update(id=base['id'],metadata=probe(folder/'source.mp4'))
    p['settings'].update(render_encoder='cpu',subtitles=False,title='',show_title=False,duck_volume=1,original_volume=1)
    r=ready(p,write(plan,p,translator,folder,lambda *a:None,lambda:None))
    for n in r['narrations']:
        n.update(audio='voice.wav',duration=.5,cues=[],caption_version=4,audio_hash=providers.voice_hash(n,r['settings']))
    timeline=build_story(r,strict=True)
    result=render_part(r,timeline,timeline['parts'][0],folder,lambda:None,width=360)
    with av.open(str(store.asset(r['id'],result['file']))) as video:
        samples=np.concatenate([f.to_ndarray().mean(axis=0) for f in video.decode(audio=0)])
        rate=video.streams.audio[0].rate
    def level(a,b,freq=None):
        x=samples[int(a*rate):int(b*rate)]
        if freq is None:return np.sqrt(np.mean(x*x))
        t=np.arange(len(x))/rate
        return abs(np.mean(x*np.exp(-2j*np.pi*freq*t)))
    assert level(.05,.4,880)>level(.05,.4,440)*30
    assert level(.8,1.5)<.001  # Original speech/tone cannot return after the AI WAV ends.
