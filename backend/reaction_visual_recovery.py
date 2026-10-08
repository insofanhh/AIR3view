"""Reserve a feasible visual schedule before prose, with bounded local recovery."""
import copy

from .reaction_visual import FPS, VERSION, VisualConstraintError, active, plan

MAX_REPAIRS = 64
MAX_WINDOW_REPAIRS = 2
MAX_VIDEO_LOSS = .10


def logical_schedule(schedule):
    clips, cursor = [], 0
    hook = schedule['hook']
    rows = ([{**hook, 'id':'hook', 'part':1, 'section':'hook'}]
            if hook['end'] > hook['start'] else []) + schedule['selections']
    for row in rows:
        a, b = round(row['start']*FPS), round(row['end']*FPS)
        end = cursor+b-a
        clips.append(dict(kind='hook' if row['id']=='hook' else 'highlight',
            start=cursor/FPS, end=end/FPS, source_start=a/FPS, source_end=b/FPS,
            part=row['part'], segment_id=row['id'], section=row['section']))
        cursor = end
    return clips


def _join(schedule, context, settings):
    """Rebalance neighbouring review windows only when they share evidence."""
    from .reaction_review import active as review_active
    from .reaction_cops import _evidence_ids
    from .reaction_commentary import members, duration
    if not review_active(settings):
        return None
    rows = schedule['selections']
    index = next((i for i,r in enumerate(rows) if r['id']==context.get('window_id')), None)
    if index is None:
        return None
    anchors = [i for i,r in enumerate(rows) if r['narration'].strip()]
    position = anchors.index(index)
    for adjacent in (position-1, position+1):
        if not 0 <= adjacent < len(anchors):
            continue
        left, right = sorted((index, anchors[adjacent]))
        lg, rg = members(schedule,left), members(schedule,right)
        if (left+len(lg)!=right or rows[left]['part']!=rows[right]['part']
                or duration(schedule,left)+duration(schedule,right)>25+.001):
            continue
        li = {c for r in lg for c in _evidence_ids(r['evidence'])}
        ri = {c for r in rg for c in _evidence_ids(r['evidence'])}
        if not li.intersection(ri):
            continue
        candidate = copy.deepcopy(schedule)
        merged = candidate['selections']
        merged[left]['commentary_span'] = len(lg)+len(rg)
        merged[left]['narration'] = '__write_review__'
        if merged[right]['section']=='ending' and merged[left]['section']!='opening':
            merged[left]['section'] = 'ending'
        merged[right].update(narration='', commentary_span=1)
        return candidate, dict(operation='join_shared_evidence', window_id=context['window_id'],
            changed_window_ids=[rows[left]['id'],rows[right]['id']],
            old_seconds=context['required_frames']/FPS,
            new_seconds=duration(schedule,left)+duration(schedule,right))
    return None


def _shorten(schedule, context, settings, cues):
    """Keep every logical row/citation; remove only surplus visual tail frames.

    Original/translated speech is never shortened. Full review has no source
    speech playback, so the pictures can end within a cue, provided each cited
    cue remains near its picture range and the entire window is re-reviewed.
    """
    from .reaction_review import active as review_active
    from .reaction_cops import _evidence_ids
    required, feasible = context.get('required_frames',0), context.get('feasible_frames',0)
    if not required or not feasible or feasible>=required or feasible < required*.65:
        return None
    ids = set(context.get('row_ids',[]))
    group = [r for r in schedule['selections'] if r['id'] in ids]
    if not group or not group[0]['narration'].strip():
        return None
    full_review = review_active(settings)
    known = {str(c['id']):c for c in cues}
    candidate = copy.deepcopy(schedule)
    remaining = required-feasible
    for row in reversed(candidate['selections']):
        if row['id'] not in ids:
            continue
        a,b = round(row['start']*FPS),round(row['end']*FPS)
        cited = [known[c] for c in _evidence_ids(row['evidence']) if c in known]
        if not cited:
            return None
        # All original cue content is protected in non-review modes.
        minimum_end = max(a+FPS, max(round(c['start']*FPS)-44 for c in cited)
                          if full_review else max(round(c['end']*FPS) for c in cited))
        removed = min(remaining,max(0,b-minimum_end))
        row['end'] = (b-removed)/FPS
        remaining -= removed
        if not remaining:
            break
    if remaining:
        return None
    return candidate, dict(operation='fit_visual_capacity', window_id=context['window_id'],
        changed_window_ids=[context['window_id']], row_ids=sorted(ids),
        old_seconds=required/FPS, new_seconds=feasible/FPS, removed_frames=required-feasible)


def fit_schedule(schedule, project, report, check):
    """Local proposals remain private until the complete picture edit passes."""
    if not active(project['settings']):
        return copy.deepcopy(schedule), []
    from . import media
    from .reaction_cops import validate_plan
    from .story import duration_budget_stats
    candidate = copy.deepcopy(project)
    media.ensure_shots(candidate,report,check,persist=False)
    result, repairs = copy.deepcopy(schedule), []
    current_total = round(logical_schedule(result)[-1]['end']*FPS)
    original_total = max(current_total,int(project.get('visual_budget',{}).get('initial_frames') or 0))
    previous_repairs = project.get('visual_repair_history',[])
    tried_joins = set()
    while True:
        check()
        candidate['story_plan'] = result
        try:
            manifest = plan(candidate,logical_schedule(result),check)
        except VisualConstraintError as error:
            if error.code not in ('insufficient_distinct_shots','frame_budget'):
                raise
            context, window = error.context,error.context.get('window_id')
            if (not window or len(repairs)+len(previous_repairs)>=MAX_REPAIRS or
                    sum(r.get('window_id')==window for r in repairs+previous_repairs)>=MAX_WINDOW_REPAIRS):
                raise
            changed = None
            if window not in tried_joins:
                tried_joins.add(window)
                changed = _join(result,context,candidate['settings'])
            if changed is None:
                changed = _shorten(result,context,candidate['settings'],candidate.get('reaction_cues',[]))
            if changed is None:
                raise
            proposed,audit = changed
            new_total = round(logical_schedule(proposed)[-1]['end']*FPS)
            if original_total-new_total > max(5*FPS,round(original_total*MAX_VIDEO_LOSS)):
                raise error
            stats = duration_budget_stats(proposed,candidate)
            if any(n < stats['minimum']-.05 for n in stats['totals'].values()):
                budget = candidate.get('reaction_duration_budget')
                if not budget:
                    raise error
                budget['effective_seconds'] = min(budget['effective_seconds'],min(stats['totals'].values()))
                budget['limiting_factor'] = 'visual_evidence'
            try:
                result = validate_plan(proposed,candidate,check_text=False)
            except (ValueError,KeyError,TypeError) as exc:
                error.context['repair_rejected'] = str(exc)
                raise error from exc
            repairs.append(audit)
            report(89,f'Cân ngân sách hình {window}: {audit["old_seconds"]:.2f}s → '
                      f'{audit["new_seconds"]:.2f}s; giữ dẫn chứng và tốc độ giọng…')
            continue
        project['shots'] = copy.deepcopy(candidate['shots'])
        project['reaction_duration_budget'] = copy.deepcopy(candidate.get('reaction_duration_budget',{}))
        project['visual_budget'] = dict(version=VERSION,status='ready',initial_frames=original_total,
            frames=round(logical_schedule(result)[-1]['end']*FPS),repairs=copy.deepcopy(repairs),
            cut_count=manifest['cut_count'])
        if repairs:
            loss=(original_total-round(logical_schedule(result)[-1]['end']*FPS))/FPS
            message=(f'Reaction COPS: đã cân {len(repairs)} cửa sổ theo hình dẫn chứng; '
                     f'lịch video giảm {loss:.2f}s; giữ setting và tốc độ giọng đã chọn.')
            if message not in project.setdefault('warnings',[]):
                project['warnings'].append(message)
        return result,repairs


def reserve(schedule, project, report, check):
    """New projects: check pictures before writing or reviewing any host prose."""
    candidate=copy.deepcopy(project)
    candidate.pop('visual_budget',None)
    candidate['visual_repair_history']=[]
    result,repairs = fit_schedule(schedule,candidate,report,check)
    for key in ('shots','reaction_duration_budget','visual_budget','warnings'):
        if key in candidate:
            project[key]=copy.deepcopy(candidate[key])
    project['visual_repair_history']=copy.deepcopy(repairs)
    return result


def recover(project, error, ask_ai, folder, report, check):
    """Existing scripts: rewrite/review only windows whose budget has changed."""
    from .reaction_dubbing import active as dubbed
    if error.code not in ('insufficient_distinct_shots','frame_budget') or dubbed(project['settings']):
        raise error
    candidate = copy.deepcopy(project)
    result,repairs = fit_schedule(candidate['story_plan'],candidate,report,check)
    if not repairs:
        return candidate
    from .reaction_review import active as review_active, write as write_review
    from .story_schedule import write_scheduled
    from .hook_policy import slots
    changed = {sid for repair in repairs for sid in repair['changed_window_ids']}
    if review_active(candidate['settings']):
        result = write_review(result,candidate,candidate.get('reaction_review_outline') or
                              {'central_question':'','chapters':[]},ask_ai,folder,report,check,
                              only_ids=changed)
    else:
        result = write_scheduled(result,candidate,ask_ai,folder,report,check,locked=True,only_ids=changed)
    old = {n.get('segment_id'):n for n in candidate['narrations']}
    narrations = []
    for slot in slots(result):
        if not slot['narration'].strip():
            continue
        sid = slot['id']
        if sid not in changed:
            narrations.append(copy.deepcopy(old[sid]))
            continue
        narration = copy.deepcopy(old.get(sid,{}))
        narration.update(id=narration.get('id','story-'+sid),segment_id=sid,
            start=slot['start'],text=slot['narration'],section=slot['section'],part=slot['part'],
            evidence=slot['evidence'],enabled=narration.get('enabled',True),
            target_duration=round(slot['end']-slot['start']-.04,3),
            audio='',audio_hash='',duration=0,cues=[],caption_version=0)
        if review_active(candidate['settings']):
            narration['speech_kind'] = 'review'
        narrations.append(narration)
    candidate.update(story_plan=result,narrations=narrations,visual_edit={},exports=[],preview_exports=[])
    from .story import plan_fingerprint, duration_plan_manifest
    from .plan_first import geometry, contract_check
    from .retention import budget
    from .reaction_commentary import audit
    fp = plan_fingerprint(candidate)
    candidate['plan_fingerprint'] = fp
    candidate['duration_plan'] = {**candidate.get('duration_plan',{}),
        **duration_plan_manifest(result,candidate),'schedule':copy.deepcopy(result),
        'geometry':geometry(result),'input_fingerprint':fp,'retention':budget(result,candidate),
        'commentary_windows':audit(result),'status':'ready',
        'visual_budget':copy.deepcopy(candidate.get('visual_budget'))}
    candidate.setdefault('visual_repair_history',[]).extend(repairs)
    for sid in changed:
        for narration in project.get('narrations',[]):
            if narration.get('segment_id')==sid:
                candidate.get('voice_repair_state',{}).pop(narration['id'],None)
    contract_check(candidate)
    return candidate
