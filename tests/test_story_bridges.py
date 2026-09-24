import copy
import pytest
from backend import story_bridges as bridges
from backend.story_schedule import schedule, write_scheduled
from backend.story import validate_plan
from backend.retention import budget
from test_retention_target import rolling_source


@pytest.mark.parametrize('ratio',[.7,.8])
def test_high_ratio_keeps_opening_development_ending_and_short_slots(ratio):
    p,raw=rolling_source();p['story_bridge_version']=bridges.VERSION
    p['settings']['original_dialogue_ratio']=ratio
    out=validate_plan(schedule(raw,p),p,check_text=False)
    voiced=[r for r in out['selections'] if r['narration']]
    assert {r['section'] for r in voiced}=={'opening','development','ending'}
    assert all(4<=r['end']-r['start']<=8 for r in voiced)
    assert ratio-.03<=budget(out,p)['actual_ratio']<=ratio


def test_excess_ai_is_not_excused_as_a_structural_reserve():
    p,raw=rolling_source();p['story_bridge_version']=bridges.VERSION
    raw['hook']['original_audio']=False
    before=budget(raw,p)['structural_reserve_seconds']
    raw['selections']=[dict(raw['selections'][0],start=i,end=i+4) for i in range(0,96,4)]
    info=budget(raw,p)
    assert info['structural_reserve_seconds']==before==12
    assert info['effective_seconds']==70


def test_long_bridge_is_split_before_validation_and_tts():
    p,raw=rolling_source();p['story_bridge_version']=bridges.VERSION
    p['settings']['original_dialogue_ratio']=.7
    raw['selections'][0]['narration']='A concise bridge.'
    normalized=bridges.normalize_slots(raw,p)
    assert all(r['end']-r['start']<=8.04 for r in normalized['selections'] if r['narration'])
    assert sum(r['end']-r['start'] for r in normalized['selections'])==sum(r['end']-r['start'] for r in raw['selections'])


@pytest.mark.parametrize('text,count',[
    ('A dispute begins. Officers arrive.',2),
    ('Mr. Lee calls Dr. Smith. The fee was 3.5 dollars.',2),
    ('争执开始了。警察赶到了。',2),
    ('The driver refuses. An officer asks. The witness answers.',3),
])
def test_sentence_limit_counts_real_sentences(text,count):
    assert bridges.sentence_count(text)==count


def review_fixture():
    p,raw=rolling_source();p['story_bridge_version']=bridges.VERSION
    raw['outcome']='The court sentenced the driver to prison.'
    plan=validate_plan(schedule(raw,p),p,check_text=False)
    for row in plan['selections']:
        if row['narration']:row['narration']=' '.join(['word']*round((row['end']-row['start'])*2.7))+'.'
    plan['hook']['narration']='A driver disputes the events after a crash that leaves lasting consequences.'
    p['summary']='A crash investigation concludes with a prison sentence.'
    return p,plan


def test_review_repairs_missing_spoken_outcome_without_recuts(tmp_path):
    p,plan=review_fixture();ending=plan['selections'][-1]
    # 8s at 2.7 units/s: a concise 20-word outcome fits the fixed slot.
    replacement='The court sentenced the driver to prison after the investigation, bringing the case to its confirmed conclusion in the source.'
    calls=[]
    def ask(prompt,*args):
        calls.append(prompt)
        assert 'metadata do NOT count' in prompt
        if len(calls)==1:return dict(complete=False,issues=['The spoken ending omits the sentence.'],repairs=[dict(id=ending['id'],text=replacement)])
        assert replacement in prompt
        return dict(complete=True,issues=[],repairs=[])
    out=bridges.review(plan,p,ask,tmp_path,lambda *a:None,lambda:None)
    assert len(calls)==2 and out['selections'][-1]['narration']==replacement
    assert [(r['start'],r['end']) for r in out['selections']]==[(r['start'],r['end']) for r in plan['selections']]
    assert plan['selections'][-1]['narration']!=replacement


def test_review_cannot_pass_metadata_only_or_patch_original_audio(tmp_path):
    p,plan=review_fixture()
    original=next(r for r in plan['selections'] if not r['narration'])
    calls=[]
    def ask(*a):
        calls.append(1)
        return dict(complete=False,issues=['Outcome missing.'],repairs=[dict(id=original['id'],text='A new invented ending.')])
    with pytest.raises(ValueError,match='STORY_COVERAGE'):
        bridges.review(plan,p,ask,tmp_path,lambda *a:None,lambda:None)
    assert len(calls)==3


def test_writer_rejects_three_sentence_reply_even_when_word_count_fits(tmp_path):
    import json
    p,plan=review_fixture();calls=[]
    def ask(prompt,*a):
        entries=json.JSONDecoder().raw_decode(prompt.split('REQUESTED SLOTS: ',1)[1])[0]
        calls.append(prompt)
        lines=[]
        for e in entries:
            words=['word']*e['target_words']
            if len(calls)==1:
                words[2]+='.';words[5]+='.';words[-1]+='.'
            else:words[-1]+='.'
            lines.append(dict(id=e['id'],text=' '.join(words)))
        return dict(items=lines)
    out=write_scheduled(plan,p,ask,tmp_path,lambda *a:None,lambda:None,locked=True)
    assert len(calls)>=2
    assert all(bridges.concise(r['narration'],'English') for r in out['selections'] if r['narration'])
