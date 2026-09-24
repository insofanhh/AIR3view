"""Cached, evidence-based speaker roles. Never infer synthetic speech from timbre."""
import json
from typing import Literal
from pydantic import Field
from .models import Model
from .source_policy import RULE, VERSION


class SpeechRole(Model):
    model_config = {'json_schema_extra':{'required':['cue','role','confidence','priority','evidence','hook_score']}}
    cue: int
    role: Literal['participant', 'commentary', 'unknown']
    confidence: float = Field(ge=0, le=1)
    priority: float = Field(ge=0, le=1)
    evidence: str = Field(min_length=1)
    hook_score: float = Field(default=0, ge=0, le=1, json_schema_extra=lambda schema:schema.pop('default',None),
                             description='Suitability as a real-source cold open: >=0.7 only for clear confrontation, urgent dramatic exchange, shout/scream or decisive spontaneous reaction. Ordinary exposition and all commentary score 0.')


class SpeechRoles(Model):
    items: list[SpeechRole]


def active(project):
    return project.get('source_speech', {}).get('version') == VERSION


def cues(project):
    return project.get('source_transcript') or project.get('transcript', [])


def participants(project):
    return [c for c in project.get('source_speech', {}).get('items', [])
            if c['role'] == 'participant' and c['confidence'] >= .7]


def allowed(project, start, end):
    """A retained interval needs complete real speech and no excluded speech."""
    rows = project.get('source_speech', {}).get('items', [])
    overlapping = [c for c in rows if c['end'] > start + .04 and c['start'] < end - .04]
    if project.get('transcript_origin') == 'youtube_auto_subtitles':
        # Rolling YouTube captions overlap across successive display updates;
        # their display end is not the end of an independent spoken sentence.
        # Still exclude EVERY overlapping commentary/unknown speaker. Only
        # allow cuts on an observed cue boundary, not arbitrary mid-word cuts.
        if not overlapping or any(c['role']!='participant' or c['confidence']<.7 for c in overlapping):
            return False
        points=[c[k] for c in rows for k in ('start','end')]
        boundary=lambda t:any(abs(t-v)<=.04 for v in points)
        starts_here=boundary(start) or min(c['start'] for c in overlapping)>=start
        ends_here=boundary(end) or max(c['end'] for c in overlapping)<=end
        return starts_here and ends_here
    return bool(overlapping) and all(c['role'] == 'participant' and c['confidence'] >= .7
        and c['start'] >= start - .04 and c['end'] <= end + .04 for c in overlapping)


def anchor(project):
    """Reserve one useful complete exchange BEFORE selecting footage, if feasible."""
    from .story import duration_budget_stats
    budget = duration_budget_stats({}, project)
    cap = min(20, budget['minimum_total'] * project['settings'].get('original_dialogue_ratio', .15))
    eligible = [c for c in participants(project) if 1 <= c['end']-c['start'] <= cap
                and c['start'] >= 4 and c['end'] <= budget['source']-4
                and allowed(project, c['start'], c['end'])]
    return max(eligible, key=lambda c: (c['priority'], c['confidence'], -c['start']), default=None)


def classify(project, ask_ai, folder, report, check):
    from .providers import digest
    rows = [dict(cue=i, start=c['start'], end=c['end'], text=c['text']) for i,c in enumerate(cues(project))]
    from .hook_policy import VERSION as HOOK_VERSION
    identity = dict(version=VERSION, hook_version=HOOK_VERSION, source=project.get('source'), rows=rows,
                    scenes=project.get('scenes', []), summary=project.get('summary', ''),
                    provider=project['settings']['provider'], model=project['settings']['model'],
                    has_audio=project['metadata'].get('has_audio', True))
    fingerprint = digest(identity)
    old = project.get('source_speech', {})
    if old.get('fingerprint') == fingerprint and old.get('version') == VERSION:
        try:
            saved=SpeechRoles.model_validate({'items':[{k:c[k] for k in SpeechRole.model_fields} for c in old['items']]})
            from .speech_recovery import partition
            valid,_=partition(saved,{r['cue'] for r in rows})
            if len(valid)==len(rows):return old
        except (ValueError,KeyError,TypeError):pass
    cache = folder / 'source-speech-cache'
    cache.mkdir(exist_ok=True)
    classified = []
    if not project['metadata'].get('has_audio', True):
        rows = []
    for offset in range(0, len(rows), 80):
        check()
        batch = rows[offset:offset+80]
        ids = {c['cue'] for c in batch}
        path = cache / f'{fingerprint}-{offset}.json'
        prompt = ('SOURCE SPEAKER ROLES v1. Classify EVERY requested cue exactly once. '
                  'participant = actual people involved speaking in the situation, interviews, phone calls, '
                  'dispatch, spontaneous reactions. Commentary = external presenter/editorial voice-over, '
                  'recap narrator (human OR AI), ads. A quoted line read by a narrator remains commentary. '
                  'For mixed participant/commentary in one cue or insufficient evidence use unknown. '
                  'Use surrounding turns and scene evidence, not first-person wording or voice timbre alone. '
                  'Give confidence, importance to the actual story (priority), and concrete role evidence. '
                  'Also score hook_score for a strong original-audio opening: real conflict, urgency, shouting, '
                  'screams explicitly evidenced in the transcript, or a decisive spontaneous reaction. '
                  'Do not infer audible screams from a still image. Normal exposition and all commentary score 0. '
                  'Sources with very little dialogue can still contain valuable real exchanges: inspect each cue. '
                  'Do not invent speakers or force any percentage.\n'+RULE+
                  '\nSUMMARY: '+project.get('summary', '')+
                  '\nSCENES: '+json.dumps(project.get('scenes', []), ensure_ascii=False)+
                  '\nCONTEXT: '+json.dumps(rows[max(0,offset-6):offset+86], ensure_ascii=False)+
                  '\nREQUESTED CUES: '+json.dumps(batch, ensure_ascii=False))
        report(85, f'Phân biệt hội thoại thật và lời bình nguồn · {offset+1}–{offset+len(batch)}/{len(rows)}…')
        answer = None
        if path.is_file():
            try:
                answer = SpeechRoles.model_validate_json(path.read_text('utf-8'))
            except (ValueError, OSError):
                pass
        from .speech_recovery import recover, partition
        if answer is None or len(partition(answer,ids)[0])!=len(ids):
            answer=recover(batch,prompt,answer,path,ask_ai,project['settings'],folder,report,check)
        else:
            valid,_=partition(answer,ids)
            answer=SpeechRoles(items=[valid[k] for k in sorted(ids)])
        by_id = {c.cue:c.model_dump() for c in answer.items}
        classified.extend({**c, **by_id[c['cue']]} for c in batch)
    return dict(version=VERSION, fingerprint=fingerprint, items=classified)


def muted_ranges(project):
    """Mute the mixed source track at excluded speech, retaining ambience elsewhere."""
    real = participants(project)
    intervals = []
    for c in project.get('source_speech', {}).get('items', []):
        if c['role'] == 'participant' and c['confidence'] >= .7:
            continue
        # Small ASR-boundary padding must not clip the adjacent real speaker.
        before = max((x['end'] for x in real if x['end'] <= c['start']),default=0)
        after = min((x['start'] for x in real if x['start'] >= c['end']),default=project['metadata']['duration'])
        intervals.append((max(before,c['start']-.12),min(after,c['end']+.12)))
    intervals.sort()
    merged = []
    for a,b in intervals:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b,merged[-1][1]))
        else:
            merged.append((a,b))
    return merged
