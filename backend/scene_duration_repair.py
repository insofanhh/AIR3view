"""Transactional frame-aligned fitting to measured audio, before final render."""
import copy
import math

MAX_ADJUSTMENTS=3


def compensate_original_ratio(candidate,changed):
    """When shrinking AI footage raises the original ratio, trim a real
    development exchange only on a validated speech boundary. Keep user limits.
    """
    from .retention import budget
    from .source_speech import allowed, active
    from .story import duration_budget_stats
    if not active(candidate):return None
    plan=candidate['story_plan'];info=budget(plan,candidate)
    ratio=candidate['settings'].get('original_dialogue_ratio',.15)
    if ratio>=1 or info['actual_ratio']<=ratio+.001:return None
    required=(info['actual_seconds']-ratio*info['total_seconds'])/(1-ratio)
    if required<=0:return None
    stats=duration_budget_stats(plan,candidate)
    cues=candidate.get('source_speech',{}).get('items',[])
    points=sorted({round(c[k]*30) for c in cues for k in ('start','end')})
    voices={n.get('segment_id') for n in candidate['narrations'] if n.get('enabled') and n.get('text','').strip()}
    affected_parts={r['part'] for r in plan['selections'] if r['id'] in changed}
    proposals=[]
    for i,row in enumerate(plan['selections']):
        if row['narration'].strip() or row['id'] in voices or row['section']!='development' or row['part'] not in affected_parts:continue
        a,b=round(row['start']*30),round(row['end']*30)
        slack=stats['totals'][row['part']]-stats['minimum']
        for point in points:
            if not a<point<b:continue
            for left,right in ((point,b),(a,point)):
                removed=((b-a)-(right-left))/30
                if right-left<30 or removed+1e-6<required or removed>slack+.001:continue
                if allowed(candidate,left/30,right/30):
                    proposals.append((removed,i,left,right))
    for removed,index,left,right in sorted(proposals):
        result=copy.deepcopy(candidate);row=result['story_plan']['selections'][index]
        old=(row['start'],row['end']);row.update(start=left/30,end=right/30)
        try:
            from .story import validate_plan
            validate_plan(result['story_plan'],result,check_text=False)
        except ValueError:continue
        return result,dict(segment_id=row['id'],old_start=old[0],old_end=old[1],new_start=left/30,new_end=right/30,
                           removed_seconds=removed,reason='keep_original_ratio_after_ai_scene_shrink')
    return None


def adjust(project,narration,measured,target,diagnostics=None):
    from .story import validate_plan, plan_fingerprint, duration_plan_manifest
    from .plan_first import geometry,contract_check
    from .retention import budget
    def reject(reason):
        if diagnostics is not None and reason not in diagnostics:diagnostics.append(reason)
    if (project['settings'].get('production_workflow')!='plan_first' or not project.get('story_plan')
            or not project['settings'].get('output_mode') or not math.isfinite(measured) or measured<=0):return None
    sid=narration.get('segment_id');rows=project['story_plan']['selections']
    index=next((i for i,r in enumerate(rows) if r.get('id')==sid),None)
    hook=sid=='hook'
    if index is None and not hook:return None
    state=project.get('voice_repair_state',{}).get(narration['id'],{})
    if len(state.get('scene_adjustments',[]))>=MAX_ADJUSTMENTS:
        reject('Đã đạt giới hạn 3 lần cân cảnh này.');return None
    try:contract_check(project)
    except (ValueError,KeyError,TypeError) as exc:
        reject(str(exc));return None
    row=project['story_plan']['hook'] if hook else rows[index]
    a,b=round(row['start']*30),round(row['end']*30)
    minimum=3 if hook or project.get('story_bridge_version') else 4
    maximum=7 if hook else 8 if project.get('story_bridge_version') else 25
    lengths=sorted((n for n in range(minimum*30,maximum*30+1)
                    if .951<=measured/(n/30-.04)<=1.049),key=lambda n:abs(n/30-.04-measured))
    touched={n['segment_id']:n for n in project['narrations'] if n.get('segment_id')}
    for length in lengths:
        if abs((length/30-.04)-target)<.04:continue
        for strategy in ('end','start','borrow_next','borrow_previous'):
            if hook and strategy.startswith('borrow'):continue
            candidate=copy.deepcopy(project);cr=candidate['story_plan']['selections']
            slot=candidate['story_plan']['hook'] if hook else cr[index]
            delta=length-(b-a);changed={sid}
            if strategy=='end':
                if delta>0:continue
                slot['end']=(a+length)/30
            elif strategy=='start':
                if delta>0:continue
                slot['start']=(b-length)/30
            else:
                ni=index+1 if strategy=='borrow_next' else index-1
                if not 0<=ni<len(cr):continue
                neighbor=cr[ni]
                if neighbor['part']!=slot['part']:continue
                na,nb=round(neighbor['start']*30),round(neighbor['end']*30)
                if (ni>index and na!=b) or (ni<index and nb!=a):continue
                voice=touched.get(neighbor['id'])
                if voice and (voice.get('audio') or not voice.get('enabled',True)):continue
                if neighbor['narration'].strip() and neighbor['evidence']!=slot['evidence']:continue
                if nb-na-delta<(90 if neighbor['narration'].strip() else 30):continue
                if ni>index:slot['end']=(b+delta)/30;neighbor['start']=(na+delta)/30
                else:slot['start']=(a-delta)/30;neighbor['end']=(nb-delta)/30
                changed.add(neighbor['id'])
            if not 0<=slot['start']<slot['end']<=candidate['metadata']['duration']:continue
            old_to_new={r['id']:r for r in cr}
            if hook:
                candidate['settings'].update(hook_start=slot['start'],hook_end=slot['end'])
                candidate['hooks']=[copy.deepcopy(slot)]+candidate.get('hooks',[])[1:]
                old_to_new['hook']=slot
            for n in candidate['narrations']:
                if n.get('segment_id') in changed:
                    s=old_to_new[n['segment_id']]
                    n.update(start=s['start'],target_duration=round(s['end']-s['start']-.04,3),audio='',audio_hash='',duration=0,cues=[],caption_version=0)
            compensation=None
            try:
                checked=validate_plan(candidate['story_plan'],candidate,check_text=False)
            except ValueError as exc:
                reject(str(exc))
                repaired=compensate_original_ratio(candidate,changed) if str(exc).startswith('Thoại gốc đang chiếm') else None
                if repaired is None:continue
                candidate,compensation=repaired
                checked=validate_plan(candidate['story_plan'],candidate,check_text=False)
                changed.add(compensation['segment_id'])
                slot=candidate['story_plan']['hook'] if hook else candidate['story_plan']['selections'][index]
            try:
                if [r['id'] for r in checked['selections']]!=[r['id'] for r in cr]:continue
                candidate['story_plan']=checked
                fp=plan_fingerprint(candidate);candidate['plan_fingerprint']=fp
                candidate['duration_plan']={**candidate['duration_plan'],**duration_plan_manifest(checked,candidate),
                    'schedule':copy.deepcopy(checked),'geometry':geometry(checked),'input_fingerprint':fp,
                    'retention':budget(checked,candidate),'status':'ready'}
                contract_check(candidate)
            except (ValueError,KeyError,TypeError) as exc:
                reject(str(exc));continue
            now=next(n for n in candidate['narrations'] if n['id']==narration['id'])
            log=dict(old_target=target,new_target=now['target_duration'],measured=measured,strategy=strategy,
                     changed_segments=sorted(changed),old_start=row['start'],old_end=row['end'],new_start=slot['start'],new_end=slot['end'])
            if compensation:log['ratio_compensation']=compensation
            repair_state=candidate.setdefault('voice_repair_state',{}).setdefault(narration['id'],{})
            repair_state.setdefault('scene_adjustments',[]).append(log)
            repair_state.update(status='scene_adjusted',last_error='',measured=measured,target=now['target_duration'])
            candidate.update(exports=[],preview_exports=[])
            return candidate
    return None


def apply_in_place(project,candidate):
    """Preserve pending narration references held by the synthesis loop."""
    original={n['id']:n for n in project['narrations']};narrations=[]
    for n in candidate['narrations']:
        destination=original.get(n['id'],{})
        destination.clear();destination.update(n);narrations.append(destination)
    project.clear();project.update(candidate);project['narrations']=narrations
