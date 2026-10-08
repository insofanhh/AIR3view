"""One commentary window can cover several unchanged output footage cuts."""
import copy
import json

VERSION = 1
MIN_SECONDS = 8
PREFERRED_SECONDS = 12
MAX_SECONDS = 25


def members(plan, index):
    rows = plan['selections']
    return rows[index:index + rows[index].get('commentary_span', 1)]


def duration(plan, index):
    return sum(round(r['end'] * 30) / 30 - round(r['start'] * 30) / 30
               for r in members(plan, index))


def slot(plan, index):
    """Virtual TTS time is output duration, never elapsed time across source gaps."""
    from .reaction_cops import _evidence_ids
    row = copy.deepcopy(plan['selections'][index])
    group = members(plan, index)
    if len(group) > 1:
        ids = list(dict.fromkeys(c for r in group for c in _evidence_ids(r['evidence'])))
        row.update(end=row['start'] + duration(plan, index),
                   evidence='SRC_CUES=' + json.dumps(ids, ensure_ascii=False) + '\n' +
                            '\n'.join(r['evidence'].split('\n', 1)[-1] for r in group),
                   source_ranges=[{'start': r['start'], 'end': r['end']} for r in group])
    return row


def validate(plan):
    """A window cannot overlap another point, cross a part, or exceed the edit."""
    rows = plan['selections']
    occupied = set()
    for i, row in enumerate(rows):
        span = row.get('commentary_span', 1)
        if not row['narration'].strip():
            if span != 1:
                raise ValueError('Cửa sổ commentary cần lời bình tại cảnh bắt đầu.')
            continue
        group = members(plan, i)
        indexes = set(range(i, i + span))
        if (len(group) != span or occupied & indexes or
                any(r['part'] != row['part'] for r in group) or
                any(r['narration'].strip() for r in group[1:])):
            raise ValueError('Cửa sổ commentary chồng nhau, vượt số cảnh hoặc ranh giới phần.')
        if duration(plan, i) > MAX_SECONDS + .001:
            raise ValueError('Mỗi commentary point cần cửa sổ tối đa 25 giây.')
        occupied.update(indexes)


def allocate(rows, anchors, desired):
    """Reserve feasible windows around editorial anchors without changing cuts.

    Group only neighbouring output clips in the same part and nearby source
    development (at most 90 source seconds, gaps at most 30 seconds). Every
    individual clip keeps its own verified cue citations. Short isolated clips
    stay original audio rather than forcing the host into a few spoken words.
    """
    for row in rows:
        row['narration'] = ''
        row.pop('commentary_span', None)
    candidates = []
    for left, first in enumerate(rows):
        seconds = 0
        for right in range(left, len(rows)):
            row = rows[right]
            if (row['part'] != first['part'] or
                    (right > left and (row['start'] - rows[right-1]['end'] > 30 or
                                       row['end'] - first['start'] > 90))):
                break
            seconds += round(row['end']*30)/30 - round(row['start']*30)/30
            if seconds > MAX_SECONDS + .001:
                break
            if seconds >= MIN_SECONDS - .001:
                candidates.append((left, right, seconds))
    occupied, chosen = set(), []

    def choose(options, anchor):
        free = [c for c in options if not occupied.intersection(range(c[0], c[1]+1))]
        if not free:
            return False
        left, right, seconds = min(free, key=lambda c: (
            0 if c[0] <= anchor <= c[1] else 1,
            min(abs(c[0]-anchor), abs(c[1]-anchor)),
            abs(c[2]-PREFERRED_SECONDS),
            -sum(r['priority'] for r in rows[c[0]:c[1]+1])/(c[1]-c[0]+1)))
        occupied.update(range(left, right+1))
        chosen.append((left, right))
        return True

    # Reserve the final evidenced turn first; earlier points must not consume it.
    for anchor in sorted(anchors, reverse=True):
        if len(chosen) >= desired:
            break
        choose([c for c in candidates if c[0] <= anchor <= c[1]], anchor)
    while len(chosen) < desired:
        free = [c for c in candidates if not occupied.intersection(range(c[0], c[1]+1))]
        if not free:
            break
        anchor = max(free, key=lambda c: (
            min((abs(rows[c[0]]['start']-rows[x]['start']) for x, _ in chosen), default=0),
            rows[c[0]]['priority']))[0]
        choose(free, anchor)
    for left, right in chosen:
        rows[left]['narration'] = '__write__'
        if right > left:
            rows[left]['commentary_span'] = right-left+1
    return rows


def audit(plan):
    return [{'segment_id': r.get('id', str(i)),
             'scene_ids': [m.get('id', str(i+j)) for j, m in enumerate(members(plan, i))],
             'seconds': round(duration(plan, i), 3),
             'source_ranges': [{'start': m['start'], 'end': m['end']} for m in members(plan, i)]}
            for i, r in enumerate(plan['selections']) if r['narration'].strip()]


def extend_to_audio(project, narration, measured, diagnostics):
    """Fit a measured voice by reserving more existing cuts, never speeding it up."""
    from .story import validate_plan, duration_plan_manifest, plan_fingerprint
    from .plan_first import geometry, contract_check
    from .retention import budget
    plan = project['story_plan']
    rows = plan['selections']
    index = next(i for i, r in enumerate(rows) if r['id'] == narration['segment_id'])
    initial = rows[index].get('commentary_span', 1)
    other_windows = {j for i, r in enumerate(rows) if i != index and r['narration'].strip()
                     for j in range(i, i+r.get('commentary_span', 1))}
    for span in range(initial+1, len(rows)-index+1):
        right = index+span-1
        row = rows[right]
        if (right in other_windows or row['part'] != rows[index]['part'] or
                row['start']-rows[right-1]['end'] > 30 or row['end']-rows[index]['start'] > 90):
            break
        seconds = sum(r['end']-r['start'] for r in rows[index:right+1])
        if seconds > MAX_SECONDS+.001:
            break
        if seconds-.04 < measured-.001:
            continue
        candidate = copy.deepcopy(project)
        candidate['story_plan']['selections'][index]['commentary_span'] = span
        try:
            checked = validate_plan(candidate['story_plan'], candidate, check_text=False)
            window = slot(checked, index)
            voice = next(n for n in candidate['narrations'] if n['id'] == narration['id'])
            voice.update(target_duration=round(window['end']-window['start']-.04, 3),
                         evidence=window['evidence'], audio='', audio_hash='', duration=0,
                         cues=[], caption_version=0)
            candidate['story_plan'] = checked
            fp = plan_fingerprint(candidate)
            candidate['duration_plan'] = {**candidate['duration_plan'], **duration_plan_manifest(checked, candidate),
                'schedule': copy.deepcopy(checked), 'geometry': geometry(checked), 'input_fingerprint': fp,
                'retention': budget(checked, candidate), 'commentary_windows': audit(checked), 'status': 'ready'}
            candidate['plan_fingerprint'] = fp
            contract_check(candidate)
            if candidate.get('shots'):
                from .reaction_visual import active as visual_active, plan as picture_plan
                from .reaction_visual_recovery import logical_schedule
                if visual_active(candidate['settings']):
                    # Do not fit a long WAV by reserving a window whose
                    # picture schedule can no longer be built afterwards.
                    picture_plan(candidate, logical_schedule(candidate['story_plan']))
        except (ValueError, KeyError, TypeError) as exc:
            if diagnostics is not None:
                diagnostics.append(str(exc))
            continue
        state = candidate.setdefault('voice_repair_state', {}).setdefault(narration['id'], {})
        state.setdefault('scene_adjustments', []).append({
            'strategy': 'extend_commentary_window', 'old_span': initial, 'new_span': span,
            'old_target': narration['target_duration'], 'new_target': voice['target_duration'],
            'measured': measured, 'changed_segments': [rows[index]['id']]})
        state.update(status='scene_adjusted', measured=measured, target=voice['target_duration'])
        candidate.update(exports=[], preview_exports=[])
        return candidate
    if diagnostics is not None:
        diagnostics.append('Không còn cảnh lân cận sạch để mở cửa sổ commentary; cần rút gọn riêng lời bình.')
    return None
