import copy
import json

import pytest

from backend.models import Settings
from backend.reaction_cops import (_planner_catalog, _clean_footage, _effective_duration,
                                   _fill_from_full_source, optimize_cues, validate_plan,
                                   validated_output_title, commentary_quality_issue,
                                   commentary_word_limit, repair_commentary_for_voice)
from backend.timeline import build_story
from backend.voice_repair import source_evidence
from backend.reaction_scene_duration import limits as scene_limits, instructions as scene_instructions, audit as scene_audit


def scene_project(lengths, gap=0, texts=None):
    settings = Settings(output_mode='single', summary_seconds=sum(lengths) + gap * (len(lengths) - 1),
                        production_workflow='plan_first', narration_style='storytelling',
                        editorial_mode='reaction_cops', reaction_scene_duration_mode='range',
                        reaction_commentary_count=2).model_dump()
    cues, roles = [], []
    start = 0
    for i, length in enumerate(lengths):
        cue = dict(id=str(i), start=start, end=start+length,
                   text=texts[i] if texts else f'Confirmed statement {i}.', source_cue_ids=[str(i)])
        cues.append(cue)
        roles.append(dict(cue=i, start=cue['start'], end=cue['end'], text=cue['text'],
                          role='participant', confidence=.9, priority=.8, evidence='real exchange', hook_score=0))
        start = cue['end'] + gap
    return {'settings': settings, 'metadata': {'duration': cues[-1]['end'], 'has_audio': True},
            'reaction_cues': cues, 'source_speech': {'version':3, 'items':roles},
            'transcript': cues, 'source': {}, 'narrations': []}


def test_scene_range_balances_whole_cues_instead_of_leaving_tiny_tail():
    project = scene_project([12, 12, 4])
    blocks = _clean_footage(project)
    assert [(b['start'], b['end']) for b in blocks] == [(0,12), (12,28)]
    assert [i for b in blocks for i in b['source_cue_ids']] == ['0','1','2']


def test_scene_range_keeps_question_with_answer_even_outside_preference():
    project = scene_project([12, 12], texts=['What happened?', 'He explained what happened.'])
    blocks = _clean_footage(project)
    assert len(blocks) == 1 and blocks[0]['end'] == 24
    report = scene_audit([{**blocks[0], 'id':'sel0'}], project['settings'])
    assert report['within_range'] == 0
    assert report['exceptions'][0]['reason'] == 'long'
    assert report['exceptions'][0]['source_end'] == 24


@pytest.mark.parametrize('role', ['commentary', 'unknown'])
def test_scene_range_does_not_merge_across_excluded_speech(role):
    project = scene_project([6, 1, 6])
    project['source_speech']['items'][1]['role'] = role
    blocks = _clean_footage(project)
    assert [b['source_cue_ids'] for b in blocks] == [['0'], ['2']]


def test_scene_range_allows_short_context_gap_but_not_long_wait():
    close = _clean_footage(scene_project([6,6], gap=.8))
    far = _clean_footage(scene_project([6,6], gap=2))
    assert len(close) == 1 and close[0]['end'] == 12.8
    assert len(far) == 2


def test_scene_range_overlapping_cues_have_no_duplicate_or_partial_footage():
    project = scene_project([12,12,4])
    project['reaction_cues'][1]['start'] = 10
    project['source_speech']['items'][1]['start'] = 10
    blocks = _clean_footage(project)
    assert [(b['start'], b['end']) for b in blocks] == [(0,24), (24,28)]
    assert [i for b in blocks for i in b['source_cue_ids']] == ['0','1','2']


def test_scene_range_preserves_long_original_exchange_without_extra_commentary():
    project = scene_project([10,16,16,10],
                            texts=['Initial statement.', 'Why did it happen?', 'Here is my answer.', 'Final statement.'])
    blocks = _clean_footage(project)
    hook = dict(start=0,end=0,title='',reason='Off',original_audio=False,narration='')
    budget = _effective_duration(project, blocks, hook)
    project['reaction_duration_budget'] = budget
    draft = {'title':'Reaction','selections':[]}
    accepted = validate_plan(_fill_from_full_source(project,draft,blocks,hook,budget), project,check_text=False)
    assert sum(bool(b['narration']) for b in accepted['selections']) == 2
    assert all(b['end']-b['start']<=25 for b in accepted['selections'] if b['narration'])
    assert any(b['end']-b['start']==32 and not b['narration'] for b in accepted['selections'])


def test_scene_range_rolling_caption_chain_does_not_become_one_long_scene():
    project = scene_project([4]*80)
    project['transcript_origin'] = 'youtube_auto_subtitles'
    for i, (cue, role) in enumerate(zip(project['reaction_cues'], project['source_speech']['items'])):
        cue.update(start=i*2, end=i*2+4)
        role.update(start=cue['start'], end=cue['end'])
    project['metadata']['duration'] = 162
    blocks = _clean_footage(project)
    assert len(blocks) > 3
    assert all(1 <= b['end'] - b['start'] <= 20 for b in blocks)
    assert all(a['end'] <= b['start'] + .04 for a,b in zip(blocks,blocks[1:]))
    by_id = {c['id']:c for c in project['reaction_cues']}
    assert all(b['start'] <= by_id[i]['start'] and by_id[i]['end'] <= b['end']
               for b in blocks for i in b['source_cue_ids'])


def test_auto_scene_mode_and_standard_mode_keep_existing_fingerprint():
    from backend.story import plan_fingerprint
    project = scene_project([10,10,10])
    project['settings']['reaction_scene_duration_mode'] = 'auto'
    original = plan_fingerprint(project)
    blocks = _clean_footage(project)
    for key in ('reaction_scene_duration_mode','reaction_scene_min_seconds','reaction_scene_max_seconds'):
        project['settings'].pop(key)
    assert plan_fingerprint(project) == original
    assert _clean_footage(project) == blocks
    assert scene_instructions(project['settings']) == ''
    project['settings']['reaction_scene_duration_mode'] = 'range'
    assert plan_fingerprint(project) == original
    ranged = plan_fingerprint(project)
    project['settings']['reaction_scene_max_seconds'] = 15
    assert plan_fingerprint(project) == ranged
    project['settings']['editorial_mode'] = 'standard'
    standard = plan_fingerprint(project)
    project['settings']['reaction_scene_min_seconds'] = 4
    assert plan_fingerprint(project) == standard
    assert scene_limits(project['settings']) is None


def test_scene_settings_validate_bounds_and_range_order():
    with pytest.raises(ValueError,match='tối thiểu'):
        Settings(reaction_scene_duration_mode='range', reaction_scene_min_seconds=21, reaction_scene_max_seconds=10)
    for key in ('reaction_scene_min_seconds','reaction_scene_max_seconds'):
        with pytest.raises(ValueError):
            Settings(**{key:26})
        with pytest.raises(ValueError):
            Settings(**{key:.5})
        assert getattr(Settings(**{key:1}), key) == 1


def test_scene_range_accepts_one_second_complete_cues():
    project = scene_project([1, 1, 1])
    project['settings'].update(reaction_scene_min_seconds=1, reaction_scene_max_seconds=1)
    blocks = _clean_footage(project)
    assert [(b['start'], b['end']) for b in blocks] == [(0, 1), (1, 2), (2, 3)]
    instructions=scene_instructions(project['settings'])
    assert 'requires 1–1 seconds per evidenced picture excerpt' in instructions
    assert 'not contiguous A/B halves' in instructions


def fixture():
    settings = Settings(output_mode='single', summary_seconds=30, duration_min_ratio=.6,
                        production_workflow='plan_first', narration_style='storytelling',
                        editorial_mode='reaction_cops', original_dialogue_ratio=.5,
                        reaction_commentary_count=2).model_dump()
    cues = [dict(id=str(i), start=i*10.0, end=(i+1)*10.0, text=f'Scene exchange {i}',
                 speaker='original', words=[]) for i in range(3)]
    roles = [dict(cue=i, start=c['start'], end=c['end'], text=c['text'], role='participant',
                  confidence=.9, priority=.8, evidence='real conversation', hook_score=0)
             for i,c in enumerate(cues)]
    project = {'settings': settings, 'metadata': {'duration':30.0,'has_audio':True},
               'source_speech': {'version':3,'items':roles},
               'reaction_cues': [{**c,'source_cue_ids':[c['id']]} for c in cues],
               'transcript':cues, 'narrations':[], 'hook_policy_version':1,
               'retention_policy_version':1}
    selections=[]
    for i,c in enumerate(cues):
        selections.append(dict(start=c['start'],end=c['end'],part=1,
            section='development',reason='confirmed turn',priority=.8,
            evidence='SRC_CUES='+json.dumps([c['id']])+'\n'+c['text'],
            narration='' if i==1 else 'Commentary point',narration_offset=0))
    plan = {'title':'Reaction','synopsis':'Confirmed exchanges',
            'outcome':'Last confirmed exchange','lesson':'No spoken lesson',
            'hook':{'start':0,'end':0,'title':'','reason':'Hook off','original_audio':False,'narration':''},
            'selections':selections}
    return project,plan


def test_reaction_title_is_rewritten_without_losing_source_claims():
    source = 'Police Stop The Wrong Shoplifter… Then He Made Things 20X Worse'
    candidate = 'Officers Detain the Wrong Shoplifting Suspect, and His Reaction Escalates Everything 20X'
    result = validated_output_title(source, candidate, 'English')
    assert result == candidate
    assert result != source


def test_reaction_title_falls_back_when_ai_copies_or_returns_plan_label():
    source = 'Police Stop The Wrong Shoplifter… Then He Made Things 20X Worse'
    copied = validated_output_title(source, source, 'English')
    technical = validated_output_title(source, 'Reaction COPS Footage Plan v2', 'English')
    assert copied != source
    assert technical != source
    assert '20X' in copied and '20X' in technical
    assert copied.startswith('What Really Happened When:')
    assert validated_output_title(source, 'Police Detain the Wrong Shoplifter, Then He Makes Everything 20X Worse', 'English') != 'Police Detain the Wrong Shoplifter, Then He Makes Everything 20X Worse'


def test_no_hook_has_no_phantom_clip_or_intro_outro():
    project,plan=fixture()
    accepted=validate_plan(plan,project,check_text=False)
    project['story_plan']=accepted
    timeline=build_story(project,strict=False)
    assert timeline['duration']==30
    assert all(c['kind']!='hook' for c in timeline['clips'])
    assert len([s for s in accepted['selections'] if s['narration']])==2


def test_reaction_cops_does_not_require_whole_video_ai_coverage(monkeypatch):
    project, plan = fixture()
    project['settings']['narration_style'] = 'storytelling'
    for index, item in enumerate(plan['selections']):
        item['id'] = f's{index}'
    project['story_plan'] = plan
    monkeypatch.setattr('backend.plan_first.contract_check', lambda *_: None)
    monkeypatch.setattr('backend.story.validate_narration_budget', lambda *_args, **_kwargs: None)
    from backend.providers import voice_hash
    project['narrations'] = []
    for item in plan['selections']:
        if not item['narration']:
            continue
        narration = dict(id='story-' + str(item['start']), segment_id=item['id'],
                         start=item['start'], text=item['narration'], enabled=True,
                         audio='voice.wav', audio_hash='', duration=.1,
                         target_duration=item['end'] - item['start'] - .04,
                         cues=[], caption_version=4)
        narration['audio_hash'] = voice_hash(narration, project['settings'])
        project['narrations'].append(narration)
    timeline = build_story(project, strict=True)
    assert timeline['narration_mix']['ai_ratio'] < .02


def test_source_narrator_cannot_support_commentary():
    project,plan=fixture()
    project['source_speech']['items'][2]['role']='commentary'
    with pytest.raises(ValueError,match='not_enough_evidence'):
        validate_plan(plan,project,check_text=False)


def test_mixed_cue_cannot_be_retained_as_original():
    project,plan=fixture()
    project['source_speech']['items'][1]['role']='unknown'
    with pytest.raises(ValueError,match='not_enough_evidence'):
        validate_plan(plan,project,check_text=False)


def test_hook_requires_explicit_toggle_and_real_source():
    project,plan=fixture()
    plan=copy.deepcopy(plan)
    plan['hook'].update(start=0,end=3,original_audio=False,narration='AI premise')
    with pytest.raises(ValueError,match='Hook'):
        validate_plan(plan,project,check_text=False)
    project['settings']['hook_enabled']=True
    project['settings']['summary_seconds']=35
    with pytest.raises(ValueError,match='Hook'):
        validate_plan(plan,project,check_text=False)


def test_real_source_hook_adds_only_its_measured_duration():
    project,plan=fixture()
    project['settings']['hook_enabled']=True
    project['settings']['summary_seconds']=33
    project['reaction_cues'][0]['end']=3
    project['source_speech']['items'][0]['end']=3
    plan['hook'].update(start=0,end=3,original_audio=True,narration='')
    accepted=validate_plan(plan,project,check_text=False)
    project['story_plan']=accepted
    timeline=build_story(project,strict=False)
    assert timeline['duration']==33
    assert timeline['clips'][0]['kind']=='hook'
    assert timeline['clips'][0]['end']==3


def test_time_citation_is_not_spoken():
    project,plan=fixture()
    plan['selections'][0]['narration']='At 12 seconds, an officer asks a question.'
    with pytest.raises(ValueError,match='mốc thời gian'):
        validate_plan(plan,project,check_text=False)


def test_one_clean_exchange_can_support_one_commentary_point():
    project,plan=fixture()
    project['settings']['summary_seconds']=10
    project['metadata']['duration']=10
    project['source_speech']['items']=project['source_speech']['items'][:1]
    project['reaction_cues']=project['reaction_cues'][:1]
    project['transcript']=project['transcript'][:1]
    plan['selections']=plan['selections'][:1]
    assert len(validate_plan(plan,project,check_text=False)['selections'])==1


def test_unsafe_srt_merge_falls_back_to_lossless_raw_cues():
    project,_=fixture()
    project['source_transcript']=project['transcript'][:2]
    def invalid_optimizer(*_args):
        return {'cues':[{'source_cue_ids':['0'],'text':'changed'}], 'warnings':[]}
    result=optimize_cues(project,invalid_optimizer,None,lambda *_:None,lambda:None)
    assert [c['source_cue_ids'] for c in result['cues']]==[['0'],['1']]
    assert [c['text'] for c in result['cues']]==[c['text'] for c in project['source_transcript']]


def test_long_source_optimization_preserves_all_cues_without_ai():
    project,_=fixture()
    original=[dict(id=str(i),start=i*2,end=i*2+1.5,text=f'I did not confirm item {i}.') for i in range(765)]
    project['source_transcript']=original
    result=optimize_cues(project,lambda *a:pytest.fail('long source optimization request'),None,lambda *a:None,lambda:None)
    assert len(result['cues'])==len(original)
    for before,after in zip(original,result['cues']):
        assert all(after[k]==before[k] for k in ('id','start','end','text'))
        assert after['source_cue_ids']==[before['id']]


def test_402_cue_incident_uses_lossless_local_cleanup_in_efficient_mode():
    project, _ = fixture()
    original = [dict(id=str(i), start=i*2, end=i*2+1.5,
                     text=f'I did not confirm item {i}.') for i in range(402)]
    project['source_transcript'] = original
    result = optimize_cues(project, lambda *a: pytest.fail('Unnecessary subtitle rewrite'), None,
                           lambda *a: None, lambda: None)
    assert len(result['cues']) == 402
    assert result['warnings'][0]['code'] == 'conservative_source_passthrough'
    assert all(all(a[k] == b[k] for k in ('id', 'start', 'end', 'text'))
               for a, b in zip(original, result['cues']))


def test_detailed_mode_still_offers_ai_cleanup_for_medium_sources():
    project, _ = fixture()
    project['settings']['analysis_workflow'] = 'detailed'
    project['source_transcript'] = [dict(id=str(i), start=i, end=i+.5, text='Confirmed.') for i in range(130)]
    calls = []
    def optimizer(prompt, *args):
        batch = json.loads(prompt.split('\nREQUESTED: ', 1)[1])
        calls.append(len(batch))
        return {'cues': [{'source_cue_ids': [c['id']], 'text': c['text']} for c in batch], 'warnings': []}
    optimize_cues(project, optimizer, None, lambda *a: None, lambda: None)
    assert calls == [128, 2]


@pytest.mark.parametrize('bad',['empty','duplicate'])
def test_long_source_optimization_rejects_corrupt_mapping(bad):
    project,_=fixture()
    project['source_transcript']=[dict(id=str(i),start=i,end=i+.5,text='Confirmed.') for i in range(501)]
    if bad=='empty':project['source_transcript'][20]['text']=''
    else:project['source_transcript'][20]['id']='0'
    with pytest.raises(ValueError):
        optimize_cues(project,lambda *a:pytest.fail('corrupt source request'),None,lambda *a:None,lambda:None)


def test_srt_token_exhaustion_splits_batch_and_preserves_every_cue():
    from backend.providers import OpenAIOutputIncomplete
    project,_=fixture()
    project['source_transcript']=[dict(id=str(i),start=float(i),end=i+.5,
                                       text=f'Scene exchange {i}') for i in range(24)]
    project['settings']['provider']='openai'
    calls=[]

    def optimizer(prompt,_images,_settings,_folder,_check,_schema):
        requested=json.loads(prompt.split('\nREQUESTED: ',1)[1])
        calls.append(len(requested))
        if len(requested)>12:
            raise OpenAIOutputIncomplete('max_output_tokens',{'reasoning_tokens':2500})
        return {'cues':[{'source_cue_ids':[c['id']],'text':c['text']} for c in requested],
                'warnings':[]}

    result=optimize_cues(project,optimizer,None,lambda *_:None,lambda:None)
    assert calls==[24,12,12]
    assert [c['source_cue_ids'][0] for c in result['cues']]==[str(i) for i in range(24)]


def test_srt_token_exhaustion_uses_raw_cues_after_bounded_splits():
    from backend.providers import OpenAIOutputIncomplete
    project,_=fixture()
    project['source_transcript']=[dict(id=str(i),start=float(i),end=i+.5,
                                       text=f'Scene exchange {i}') for i in range(24)]
    project['settings']['provider']='openai'
    calls=[]

    def optimizer(prompt,*_args):
        requested=json.loads(prompt.split('\nREQUESTED: ',1)[1])
        calls.append(len(requested))
        raise OpenAIOutputIncomplete('max_output_tokens')

    result=optimize_cues(project,optimizer,None,lambda *_:None,lambda:None)
    assert calls==[24,12,6,6,12,6,6]
    assert [c['text'] for c in result['cues']]==[c['text'] for c in project['source_transcript']]
    assert len(project['warnings'])==4


def test_duration_repair_receives_only_cited_in_scene_evidence():
    project,plan=fixture()
    project['summary']='Narrator claims a later arrest'
    project['reaction_cues'].append({'id':'3','source_cue_ids':['3'],'start':2,'end':4,
                                     'text':'Narrator claims a later arrest'})
    project['source_speech']['items'].append({'cue':3,'start':2,'end':4,'text':'Narrator claims a later arrest',
        'role':'commentary','confidence':.99,'priority':0,'evidence':'voice-over','hook_score':0})
    evidence=source_evidence(project,{'evidence':plan['selections'][0]['evidence'],'start':0,'target_duration':10})
    assert 'Scene exchange 0' in evidence
    assert 'later arrest' not in evidence


@pytest.mark.parametrize('scene_mode,scene_min', [('auto',10), ('range',10), ('range',15)])
def test_reaction_plan_first_pipeline_keeps_hook_off_and_only_eligible_cues(monkeypatch,tmp_path,scene_mode,scene_min):
    from backend import providers, store
    from backend.reaction_cops import plan_reaction
    project, _ = fixture()
    project['settings']['reaction_commentary_count']=5
    project['settings']['reaction_scene_duration_mode'] = scene_mode
    project['settings']['reaction_scene_min_seconds'] = scene_min
    project.update(id='reaction-test',source='fixture.mp4',summary='Unverified narrator claim',
                   scenes=[],hooks=[],exports=[],preview_exports=[],warnings=[])
    project['voice_repair_state'] = {'story-sel0': {'approved_text': 'Old copied dialogue'}}
    project['settings']['provider']='openai'
    project['source_transcript']=project['transcript']
    monkeypatch.setattr(store,'project_dir',lambda _id:tmp_path)
    # This fixture tests provider/planner contracts without a media file. The
    # real reserve/solver integration is covered in test_reaction_visual_recovery.
    monkeypatch.setattr('backend.reaction_visual_recovery.reserve',lambda plan,*args:plan)
    seen=[]
    plan_calls=[]
    def fake_ai(prompt,_frames,_settings,_folder,_check,schema):
        seen.append((schema.__name__,prompt))
        if schema.__name__=='OptimizedBatch':
            return {'cues':[{'source_cue_ids':[c['id']],'text':c['text']} for c in project['transcript']],
                    'warnings':[]}
        if schema.__name__=='SpeechRoles':
            return {'items':[{'cue':i,'role':'participant','confidence':.9,'priority':.8,
                              'evidence':'direct exchange','hook_score':0} for i in range(3)]}
        if schema.__name__=='ReactionFootagePlan':
            plan_calls.append(1)
            if len(plan_calls)==1:
                raise providers.OpenAIRequestTooLarge(10000,11977)
            return {'title':'Reaction','synopsis':'Confirmed exchanges','last_confirmed_event':'Third exchange',
                    'selections':[{'start':i*10,'end':(i+1)*10,'part':1,'section':'development',
                                   'reason':'confirmed exchange','priority':.8,'source_cue_ids':[str(i)],
                                   'keep_original':i==1} for i in range(3)]}
        if schema.__name__=='ReactionTitle':
            return {'title':'Confirmed on-scene exchanges'}
        if schema.__name__=='ScheduledNarration':
            rows=json.loads(prompt.split('\nREQUESTED SLOTS: ',1)[1].split('\nACCEPTED',1)[0])
            return {'items':[{'id':row['id'],'text':' '.join(['diễn biến']*36)} for row in rows]}
        raise AssertionError(schema.__name__)
    monkeypatch.setattr(providers,'ask_ai',fake_ai)
    result=plan_reaction(project,lambda *_:None,lambda:None)
    assert result['story_plan']['hook']['end']==0
    assert result['settings']['hook_enabled'] is False
    expected_points = 3
    assert len(result['narrations'])==expected_points
    assert result['duration_plan']['status']=='ready'
    assert len(plan_calls)==2
    assert 'Request 5 distinct commentary points' in next(p for name,p in seen if name=='ReactionFootagePlan')
    assert result['duration_plan']['reaction_budget']['requested_commentary_count']==5
    assert result['duration_plan']['reaction_budget']['actual_commentary_count']==expected_points
    assert result['voice_repair_state'] == {}
    assert any(f'{expected_points}/5 commentary points' in warning for warning in result['warnings'])
    if scene_mode == 'range':
        stats = result['duration_plan']['reaction_scene_duration']
        assert stats['within_range'] == (3 if scene_min == 10 else 0)
        assert f'picture edit requires {scene_min}–20' in next(p for name,p in seen if name=='ReactionFootagePlan')
        assert f'picture edit requires {scene_min}–20' in next(p for name,p in seen if name=='ScheduledNarration')
        if scene_min == 15:
            assert len(stats['exceptions']) == 3
            assert stats['track'] == 'logical_evidence'
    else:
        assert 'reaction_scene_duration' not in result['duration_plan']
        assert 'OPTIONAL SCENE DURATION' not in next(p for name,p in seen if name=='ReactionFootagePlan')
    assert 'Unverified narrator claim' not in next(p for name,p in seen if name=='ReactionFootagePlan')
    assert 'Unverified narrator claim' not in next(p for name,p in seen if name=='ScheduledNarration')


def test_long_source_planner_catalog_is_bounded_and_spans_source():
    catalog=[{'id':str(i),'start':i*10,'end':i*10+2,'priority':(i%7)/7,
              'text':'x'*500} for i in range(1000)]
    picked=_planner_catalog(catalog,10000)
    assert len(picked)<=82
    assert picked[0]['id']=='0' and picked[-1]['id']=='999'
    assert all(len(c['text'])<=220 for c in picked)
    assert len({int(c['start']//1000) for c in picked})==10


def _many_clean_cues(count, requested=240):
    project,_=fixture()
    project['settings'].update(summary_seconds=requested, duration_min_ratio=.9,
                               original_dialogue_ratio=.7, reaction_commentary_count=5)
    cues=[dict(id=str(i), start=round(i*2.1, 3), end=round(i*2.1+2, 3),
               text=f'Confirmed in-scene exchange {i}', source_cue_ids=[str(i)], priority=.8)
          for i in range(count)]
    project['reaction_cues']=cues
    project['transcript']=cues
    project['metadata']['duration']=cues[-1]['end']+.1
    project['source_speech']['items']=[dict(cue=i,start=c['start'],end=c['end'],text=c['text'],
        role='participant',confidence=.95,priority=.8,evidence='direct exchange',hook_score=0)
        for i,c in enumerate(cues)]
    return project


def _short_ai_draft():
    return {'title':'Confirmed developments','synopsis':'Verified exchanges',
            'last_confirmed_event':'A participant continues speaking.',
            'selections':[{'start':0,'end':2,'part':1,'section':'development',
                           'reason':'Opening exchange','priority':.8,
                           'source_cue_ids':['0'],'keep_original':True}]}


def test_sampled_cues_cannot_falsely_exhaust_full_footage_budget():
    project=_many_clean_cues(120)
    sampled=_planner_catalog(project['reaction_cues'],project['metadata']['duration'],4,8)
    assert sum(c['end']-c['start'] for c in sampled)<216
    blocks=_clean_footage(project)
    hook=dict(start=0,end=0,title='',reason='Hook off',original_audio=False,narration='')
    budget=_effective_duration(project,blocks,hook)
    assert budget['available_seconds']>216
    assert budget['effective_seconds']==240
    project['reaction_duration_budget']=budget
    repaired=_fill_from_full_source(project,_short_ai_draft(),blocks,hook,budget)
    accepted=validate_plan(repaired,project,check_text=False)
    actual=sum(c['end']-c['start'] for c in accepted['selections'])
    assert 216<=actual<=240.05
    assert 1<=sum(bool(c['narration']) for c in accepted['selections'])<=10


def test_true_source_shortfall_lowers_effective_target_without_changing_setting():
    project=_many_clean_cues(25)
    blocks=_clean_footage(project)
    hook=dict(start=0,end=0,title='',reason='Hook off',original_audio=False,narration='')
    budget=_effective_duration(project,blocks,hook)
    project['reaction_duration_budget']=budget
    assert budget['requested_seconds']==240
    assert 50<budget['effective_seconds']<60
    repaired=_fill_from_full_source(project,_short_ai_draft(),blocks,hook,budget)
    accepted=validate_plan(repaired,project,check_text=False)
    assert project['settings']['summary_seconds']==240
    assert sum(c['end']-c['start'] for c in accepted['selections'])>=budget['effective_seconds']*.9


def test_requested_duration_is_not_capped_by_soft_dialogue_ratio():
    project=_many_clean_cues(1000,requested=1800)
    blocks=_clean_footage(project)
    hook=dict(start=0,end=0,title='',reason='Hook off',original_audio=False,narration='')
    budget=_effective_duration(project,blocks,hook)
    assert budget['available_seconds']>1800
    assert budget['effective_seconds']==1800
    assert project['settings']['summary_seconds']==1800


def test_full_source_repair_covers_last_chapter_even_with_high_dialogue_target():
    project=_many_clean_cues(120)
    project['settings']['original_dialogue_ratio']=1.0
    blocks=_clean_footage(project)
    hook=dict(start=0,end=0,title='',reason='Hook off',original_audio=False,narration='')
    budget=_effective_duration(project,blocks,hook)
    project['reaction_duration_budget']=budget
    result=validate_plan(_fill_from_full_source(project,_short_ai_draft(),blocks,hook,budget),
                         project,check_text=False)
    voiced=[x for x in result['selections'] if x['narration']]
    assert 3 <= len(voiced) <= 6
    assert voiced[-1]['start'] > result['selections'][-1]['start']*.65
    assert result['selections'][-1]['end']==pytest.approx(blocks[-1]['end'], abs=.04)
    assert result['outcome']==project['reaction_cues'][-1]['text']


def test_reaction_ratio_is_soft_but_measured():
    from backend.retention import budget,validate
    project,plan=fixture()
    project['settings']['original_dialogue_ratio']=.8
    validate(plan,project)
    measured=budget(plan,project)
    assert measured['requested_ratio']==.8
    assert measured['actual_ratio']<.8


def test_reaction_pipeline_repairs_underfilled_ai_draft_from_full_source(monkeypatch,tmp_path):
    from backend import providers,store
    from backend.reaction_cops import plan_reaction

    project=_many_clean_cues(120)
    project.update(id='reaction-full-catalog',source='fixture.mp4',source_transcript=project['transcript'],
                   summary='',scenes=[],hooks=[],exports=[],preview_exports=[],warnings=[])
    project['settings']['provider']='openai'
    monkeypatch.setattr(store,'project_dir',lambda _id:tmp_path)
    calls=[]

    def fake_ai(prompt,_frames,_settings,_folder,_check,schema):
        calls.append(schema.__name__)
        if schema.__name__=='OptimizedBatch':
            rows=json.loads(prompt.split('\nREQUESTED: ',1)[1])
            return {'cues':[{'source_cue_ids':[c['id']],'text':c['text']} for c in rows],
                    'warnings':[]}
        if schema.__name__=='SpeechRoles':
            rows=json.loads(prompt.split('\nREQUESTED CUES: ',1)[1])
            return {'items':[{'cue':c['cue'],'role':'participant','confidence':.95,
                              'priority':.8,'evidence':'direct exchange','hook_score':0}
                             for c in rows]}
        if schema.__name__=='ReactionFootagePlan':
            return _short_ai_draft()
        if schema.__name__=='ReactionTitle':
            return {'title':'Confirmed on-scene exchanges'}
        if schema.__name__=='ScheduledNarration':
            rows=json.loads(prompt.split('\nREQUESTED SLOTS: ',1)[1].split('\nACCEPTED',1)[0])
            return {'items':[{'id':c['id'],'text':'Cảnh '*c['min_words']} for c in rows]}
        raise AssertionError(schema.__name__)

    monkeypatch.setattr(providers,'ask_ai',fake_ai)
    result=plan_reaction(project,lambda *_:None,lambda:None)
    budget=result['duration_plan']['reaction_budget']
    assert calls.count('ReactionFootagePlan')==1
    assert budget['requested_seconds']==budget['effective_seconds']==240
    assert 216<=budget['actual_seconds']<=240
    assert result['duration_plan']['status']=='ready'
    assert len(result['narrations'])>=1


def test_token_limited_planner_uses_verified_full_source(monkeypatch,tmp_path):
    from backend import providers,store
    from backend.reaction_cops import plan_reaction

    project=_many_clean_cues(25)
    project.update(id='reaction-token-fallback',source='fixture.mp4',
                   source_transcript=project['transcript'],summary='',scenes=[],hooks=[],
                   exports=[],preview_exports=[],warnings=[])
    project['settings']['provider']='openai'
    monkeypatch.setattr(store,'project_dir',lambda _id:tmp_path)
    calls=[]

    def fake_ai(prompt,_frames,_settings,_folder,_check,schema):
        calls.append(schema.__name__)
        if schema.__name__=='OptimizedBatch':
            rows=json.loads(prompt.split('\nREQUESTED: ',1)[1])
            return {'cues':[{'source_cue_ids':[c['id']],'text':c['text']} for c in rows],
                    'warnings':[]}
        if schema.__name__=='SpeechRoles':
            rows=json.loads(prompt.split('\nREQUESTED CUES: ',1)[1])
            return {'items':[{'cue':c['cue'],'role':'participant','confidence':.95,
                              'priority':.8,'evidence':'direct exchange','hook_score':0}
                             for c in rows]}
        if schema.__name__=='ReactionFootagePlan':
            raise providers.OpenAIRequestTooLarge(10000,11977)
        if schema.__name__=='ReactionTitle':
            return {'title':'Confirmed on-scene exchanges'}
        if schema.__name__=='ScheduledNarration':
            rows=json.loads(prompt.split('\nREQUESTED SLOTS: ',1)[1].split('\nACCEPTED',1)[0])
            return {'items':[{'id':c['id'],'text':'Cảnh '*c['min_words']} for c in rows]}
        raise AssertionError(schema.__name__)

    monkeypatch.setattr(providers,'ask_ai',fake_ai)
    result=plan_reaction(project,lambda *_:None,lambda:None)
    assert calls.count('ReactionFootagePlan')==4
    assert result['duration_plan']['reaction_budget']['effective_seconds']<240
    assert result['duration_plan']['status']=='ready'
    assert any('tự điều chỉnh' in warning for warning in result['warnings'])


@pytest.mark.parametrize('requested', [1, 2, 5, 10])
def test_commentary_setting_controls_full_source_repair(requested):
    project = _many_clean_cues(120)
    project['settings']['reaction_commentary_count'] = requested
    blocks = _clean_footage(project)
    hook = dict(start=0,end=0,title='',reason='Hook off',original_audio=False,narration='')
    budget = _effective_duration(project,blocks,hook)
    project['reaction_duration_budget'] = budget
    plan = validate_plan(_fill_from_full_source(project,_short_ai_draft(),blocks,hook,budget),
                         project,check_text=False)
    commentary = [s for s in plan['selections'] if s['narration']]
    assert len(commentary) == requested
    assert commentary[-1]['start'] > plan['selections'][-1]['start']*.65


def test_commentary_setting_rejects_out_of_range_and_invalidates_only_reaction_plan():
    from backend.story import plan_fingerprint
    for value in (-1, 11):
        with pytest.raises(ValueError):
            Settings(editorial_mode='reaction_cops',reaction_commentary_count=value)
    project,_ = fixture()
    project.update(source='fixture.mp4',scenes=[],summary='')
    before = plan_fingerprint(project)
    project['settings']['reaction_commentary_count'] = 3
    assert plan_fingerprint(project) != before
    after = plan_fingerprint(project)
    project['settings']['original_dialogue_ratio'] = .9
    assert plan_fingerprint(project) == after


def test_commentary_setting_allows_evidence_limited_shortfall():
    project,_ = fixture()
    project['settings']['reaction_commentary_count'] = 10
    blocks = _clean_footage(project)
    hook = dict(start=0,end=0,title='',reason='Hook off',original_audio=False,narration='')
    budget = _effective_duration(project,blocks,hook)
    project['reaction_duration_budget'] = budget
    plan = validate_plan(_fill_from_full_source(project,_short_ai_draft(),blocks,hook,budget),
                         project,check_text=False)
    assert sum(bool(s['narration']) for s in plan['selections']) == len(blocks) < 10


def test_commentary_audit_rejects_character_voice_and_repeated_prose():
    source = ("He touched me and he said I touched him. I did not. "
              "He touched me again before I spoke.")
    copied = "He touched me, and he said I touched him. I did not. He touched me again."
    assert 'một hoặc hai câu' in commentary_quality_issue(copied, source, 6, 'English')
    one_voice = "He touched me and he said I touched him; I did not touch him."
    assert 'đọc lại lời nhân vật' in commentary_quality_issue(one_voice, source, 6, 'English')
    repeated = ('The witness describes a confrontation at the exit, and officers ask what followed. '
                'He says she kept coming at him while he backed away, and she kept coming at him.')
    assert 'lặp lại' in commentary_quality_issue(repeated, 'She approached the exit.', 14, 'English')
    valid = ('The account now shifts from the items to the reported physical confrontation. '
             'Officers still need to connect that claim with the other accounts before deciding what happened.')
    assert commentary_quality_issue(valid, source, 14, 'English') is None


def test_commentary_preflight_repairs_only_rejected_point_and_syncs_plans(monkeypatch, tmp_path):
    from backend import store
    project, plan = fixture()
    project['settings']['language'] = 'English'
    project.update(id='a' * 32, story_plan=copy.deepcopy(plan),
                   duration_plan={'schedule': copy.deepcopy(plan)},
                   voice_repair_state={}, exports=['old'], preview_exports=['old'])
    selection = project['story_plan']['selections'][2]
    selection['id'] = 'sel22'
    project['duration_plan']['schedule']['selections'][2]['id'] = 'sel22'
    long_text = ('The officers continue asking about what happened at the entrance while '
                 'the witness gives a longer account and everyone remains nearby as they '
                 'try to work out exactly which parts of the claim can be confirmed.')
    narration = dict(id='story-sel22', segment_id='sel22', start=20.0, evidence=selection['evidence'],
                     target_duration=4.96, text=long_text, audio='', audio_hash='', duration=0,
                     cues=[], caption_version=0)
    untouched = dict(id='story-sel0', text='An earlier confirmed turn.', audio='voices/ok.wav')
    project['narrations'] = [untouched, narration]
    saved = []
    monkeypatch.setattr(store, 'save', lambda value: saved.append(copy.deepcopy(value)) or value)
    prompts = []
    def answer(prompt, _frames, _settings, _folder, _check, schema):
        prompts.append(prompt)
        if len(prompts) == 1:
            return {'text': long_text}
        return {'text': 'The witness gives an account. Officers still need to check it.'}
    assert repair_commentary_for_voice(project, narration, answer, tmp_path,
                                       lambda *_: None, lambda: None)
    assert len(prompts) == 2
    assert 'at most 18 words' in prompts[0]
    assert project['voice_repair_state']['story-sel22']['commentary_generation'] == 2
    assert project['story_plan']['selections'][2]['narration'] == narration['text']
    assert project['duration_plan']['schedule']['selections'][2]['narration'] == narration['text']
    assert project['narrations'][0] == untouched
    assert project['exports'] == [] and project['preview_exports'] == []
    assert saved


def test_commentary_word_limit_uses_spoken_slot_not_full_clip():
    assert commentary_word_limit(10.01, 'English') == 37
    assert commentary_word_limit(10.01 - .04, 'English') == 36


def test_commentary_preflight_stops_after_bounded_retries_without_changing_text(monkeypatch, tmp_path):
    from backend import store
    project, _ = fixture()
    project['settings']['language'] = 'English'
    project['id'] = 'b' * 32
    narration = dict(id='story-sel22', segment_id='sel22', start=20.0, evidence='SRC_CUES=["2"]\nScene exchange 2',
                     target_duration=3.96, text='The officers ask the witness to explain what happened near the entrance while the account remains disputed.',
                     audio='', audio_hash='', duration=0, cues=[])
    original = narration['text']
    monkeypatch.setattr(store, 'save', lambda value: value)
    generations = []
    def unchanged(prompt, *_args):
        generations.append(prompt.split('GENERATION ', 1)[1].split('.', 1)[0])
        return {'text': original}
    with pytest.raises(ValueError, match='Đã thử sửa riêng đoạn này 4 lần'):
        repair_commentary_for_voice(project, narration, unchanged, tmp_path,
                                    lambda *_: None, lambda: None)
    assert generations == ['1', '2', '3', '4']
    assert narration['text'] == original
    assert project['voice_repair_state']['story-sel22']['status'] == 'commentary_repair_failed'


def test_last_commentary_stays_near_final_confirmed_scene():
    project = _many_clean_cues(120)
    blocks = _clean_footage(project)
    hook = dict(start=0, end=0, title='', reason='Hook off', original_audio=False, narration='')
    budget = _effective_duration(project, blocks, hook)
    project['reaction_duration_budget'] = budget
    plan = _fill_from_full_source(project, _short_ai_draft(), blocks, hook, budget)
    total = sum(row['end']-row['start'] for row in plan['selections'])
    elapsed = 0
    last_end = 0
    for row in plan['selections']:
        elapsed += row['end']-row['start']
        if row['narration']:
            last_end = elapsed
    assert total-last_end <= 20
    voiced = [row for row in plan['selections'] if row['narration']]
    voiced[-1]['narration'] = ''
    with pytest.raises(ValueError, match='COMMENTARY cuối'):
        validate_plan(plan, project, check_text=False)


def test_writer_retries_copied_character_speech_before_approving(tmp_path):
    from backend.story_schedule import write_scheduled
    project, plan = fixture()
    project['settings']['language'] = 'English'
    source = ('The woman said she was on probation, then asked whether she had '
              'to go to jail because of the bag.')
    project['reaction_cues'][0]['text'] = source
    project['source_speech']['items'][0]['text'] = source
    plan['selections'][0]['evidence'] = 'SRC_CUES=["0"]\n' + source
    for row in plan['selections'][1:]:
        row['narration'] = ''
    accepted = ('The encounter shifts from the disputed bag to the consequences she fears. '
                'Officers still need to clarify the reported contact before deciding what follows from that exchange.')
    calls = []
    def ask(prompt, *_args):
        calls.append(prompt)
        ids = json.loads(prompt.split('\nREQUESTED SLOTS: ', 1)[1].split('\nACCEPTED', 1)[0])
        text = source if len(calls) == 1 else accepted
        return {'items': [{'id': row['id'], 'text': text} for row in ids]}
    result = write_scheduled(plan, project, ask, tmp_path, lambda *_: None, lambda: None, locked=True)
    assert len(calls) == 2
    assert result['selections'][0]['narration'] == accepted
