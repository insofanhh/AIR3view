import json
import pytest
from backend.voice_repair import repair_text, NarrationReply


ORIGINAL='At the chaotic accident scene, an uncooperative suspect struggles against law enforcement officers while demanding answers.'
SHORT='At the chaotic scene, an uncooperative suspect struggles.'
FITTED='At the accident scene, an uncooperative suspect struggles against law enforcement officers while demanding answers.'


def test_real_failed_hook_rejects_whole_paragraph_deletion_then_rewrites(tmp_path):
    replies=iter([SHORT,ORIGINAL,FITTED]);prompts=[]
    def ask(prompt,*a):
        prompts.append(prompt)
        return {'text':next(replies)}
    result=repair_text(ORIGINAL,'Existing source evidence','English',5.96,6.32,
        {'production_workflow':'plan_first'},tmp_path,lambda:None,ask,max_attempts=3)
    assert result==FITTED
    assert 'NARRATION LOCAL EDIT v5' in prompts[1]
    assert 'NARRATION DURATION REPAIR v5' in prompts[2]
    assert 'COMPLETE revised paragraph' in prompts[2]
    audit=json.loads(next((tmp_path/'voice-repair-attempts').glob('*.json')).read_text('utf-8'))
    assert audit['attempts'][1]['operation']=='remove'
    assert audit['attempts'][1]['candidate'] is None
    assert audit['attempts'][2]['candidate']==FITTED


def test_full_paragraph_never_becomes_an_empty_candidate(tmp_path):
    replies=iter([SHORT,ORIGINAL])
    with pytest.raises(ValueError,match='quá lớn'):
        repair_text(ORIGINAL,'Evidence','English',5.96,6.32,
            {'production_workflow':'plan_first'},tmp_path,lambda:None,lambda *a:{'text':next(replies)},max_attempts=2)
    audit=json.loads(next((tmp_path/'voice-repair-attempts').glob('*.json')).read_text('utf-8'))
    assert not any(x['candidate']=='' for x in audit['attempts'])


def test_local_deletion_still_accepts_one_safe_modifier():
    replies=iter([SHORT,'chaotic'])
    assert repair_text(ORIGINAL,'Evidence','English',5.96,6.32,
        {'production_workflow':'plan_first'},None,lambda:None,lambda *a:{'text':next(replies)})==FITTED


@pytest.mark.parametrize('text',[' ','\n\t',''])
def test_whitespace_is_rejected_before_edit_application(text):
    with pytest.raises(ValueError):NarrationReply.model_validate({'text':text})


def test_append_contract_cannot_duplicate_existing_paragraph():
    original='The officer follows the person through the street.'
    whole=original+' The camera records the encounter.'
    replies=iter(['Short.',whole,whole])
    prompts=[]
    def ask(prompt,*a):prompts.append(prompt);return {'text':next(replies)}
    result=repair_text(original,'Source camera records this encounter','English',6,4,
        {'production_workflow':'plan_first'},None,lambda:None,ask,max_attempts=3)
    assert result==whole and result.count(original)==1
    assert len(prompts)==3
