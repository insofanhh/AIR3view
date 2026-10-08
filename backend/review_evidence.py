"""Cue-level facts and durable, evidence-keyed repair checkpoints.

Raw utterances are evidence of what was said, not proof of their premise.
Only the independent reviewer can approve the resulting host paragraph.
"""
import hashlib
import json
import re
import time
import uuid


def utterance_ledger(cues):
    result=[]
    for cue in cues:
        text=cue['text'].strip()
        conditional=bool(re.search(r'\b(if|might|may|could|possibly|would)\b|có thể|nếu',text,re.I))
        question='?' in text
        result.append(dict(source_cue_ids=[cue['id']],utterance=text,
            status='question_not_fact' if question else 'conditional_or_uncertain' if conditional else 'spoken_statement',
            constraint='Attribute to the speaker; preserve modality, negation and numbers. '
                       'No inferred request, motive, legal outcome or causal link.'))
    return result


def checkpoint_key(entry,settings):
    # Unrelated grouping/outline edits must not discard accepted neighbouring
    # paragraphs. All actual evidence and timing/style/model inputs are keyed.
    payload=dict(version=1,entry=entry,language=settings['language'],rule=settings['draft_rule'],
                 model=settings['model'],provider=settings['provider'])
    return hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def load_checkpoint(path):
    try:
        state=json.loads(path.read_text('utf-8'))
        if isinstance(state,dict) and state.get('version')==1:
            return state
    except (OSError,ValueError):
        pass
    return dict(version=1,attempts=0,accepted='',rejections=[])


def save_checkpoint(path,state):
    path.parent.mkdir(exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(state,ensure_ascii=False,indent=2),'utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def record_rejection(state,text,issue,contract=None):
    state.setdefault('rejections',[]).append(dict(text=text,issue=issue,
        contract=contract or {},time=time.time()))
    state['rejections']=state['rejections'][-8:]
    state['accepted']=''


def repair_constraints(state):
    history=state.get('rejections',[])
    return dict(previous_attempts=state.get('attempts',0),
        rejected=[dict(text=r['text'],issue=r['issue']) for r in history[-3:]],
        evidence_contract=[r['contract'] for r in history if r.get('contract')][-3:],
        instructions='Remove the exact unsupported propositions. Keep the confirmed facts and warnings. '
                     'A promised action is not a request; possible charges are not filed charges. '
                     'Keep independent facts separate unless causality is explicitly evidenced. '
                     'Use fewer claims and sentences for sparse evidence; do not fill time with invention.')
