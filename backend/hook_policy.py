"""One audible hook: a real source highlight, otherwise a timed AI premise."""
import copy

VERSION = 1
RULE = '''HOOK AND RECAP STYLE v1:
Start with the strongest evidenced real confrontation, urgent exchange, shout,
scream or spontaneous reaction with original on-scene sound. No source narrator,
synthetic commentary, music-only montage, fabricated screams or misleading cuts.
The source hook is 3-7 seconds, with enough context to understand the reaction.
Never mute a selected real hook just to satisfy a dialogue percentage.
If no suitable source-audio moment is evidenced, write ONE concise AIR3view hook
sentence: who is involved + overall situation + the central conflict/stakes or
evidenced reversal. It must frame the whole story, not describe an isolated frame.
No greeting, timestamps, generic 'unbelievable/watch to the end', invented suspense,
or list of every event. Use the same narrator, pace and voice as the rest of the video.
Recap style: introduce the core tension early; then explain the trigger, escalating
actions, key evidence and actual outcome in connected chronological sentences.
Use concrete subjects and active verbs. One meaningful development per sentence.
Move from hook to necessary context without repeating the hook sentence.
Briefly alternate narration with decisive real exchanges when available. Do not
copy the reference videos' wording, identities, claims or sound effects.
Distinguish allegations from established facts. Preserve uncertainty and attribution.
Conclude with the supported outcome; an open question is optional only when the
evidence leaves a real issue unresolved, never a forced engagement-bait accusation.'''


def source_hook(project):
    from .source_speech import participants, allowed
    if not project['metadata'].get('has_audio', True):
        return None
    duration = project['metadata']['duration']
    candidates=[]
    for cue in participants(project):
        if cue.get('hook_score',0) < .7 or cue['end']-cue['start'] > 7:
            continue
        length=max(3,round((cue['end']-cue['start'])*30)/30)
        # Try balanced and one-sided context; never cut into excluded speech.
        for start in (cue['start']-(length-(cue['end']-cue['start']))/2, cue['start'],cue['end']-length):
            a=round(max(0,min(start,duration-length))*30)/30
            b=round((a+length)*30)/30
            if 0<=a<b<=duration+.001 and allowed(project,a,b):
                candidates.append((cue['hook_score'],cue['priority'],cue['confidence'],-a,
                    dict(start=a,end=b,title='Hook',reason=cue['evidence'],original_audio=True,narration='')))
                break
    return max(candidates,key=lambda x:x[:4])[-1] if candidates else None


def prepare_hook(proposed, project):
    chosen=source_hook(project)
    if chosen:
        return {**chosen,'title':proposed['title']}
    return {**copy.deepcopy(proposed),'original_audio':False,'narration':'__write_hook__'}


def slots(plan):
    """Uniform source-time slots for TTS/contracts, separate from story geometry."""
    result=list(plan.get('selections',[]))
    hook=plan.get('hook',{})
    if not hook.get('original_audio',True) and hook.get('narration','').strip():
        result.insert(0,dict(id='hook',start=hook['start'],end=hook['end'],part=1,section='hook',
                             evidence=hook['reason'],narration=hook['narration'],narration_offset=0))
    return result


def set_text(plan, segment_id, text):
    if segment_id=='hook':
        plan['hook']['narration']=text
    else:
        for row in plan.get('selections',[]):
            if row.get('id')==segment_id:
                row['narration']=text


def original_limit(project,total,hook):
    """The mandatory real hook is the only minimum when a very short edit is capped."""
    cap=total*project['settings'].get('original_dialogue_ratio',.15)
    if hook.get('original_audio',True):
        cap=max(cap,hook['end']-hook['start'])
    return cap
