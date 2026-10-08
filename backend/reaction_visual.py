"""An independent, frame-exact picture edit over locked editorial/audio windows.

Source shots are detected from pixels, never invented by dividing selections.
Distinct cited exchanges in a continuous shot may supply separate excerpts,
but contiguous halves of that shot never count as a picture change.
"""
import copy
import math
import time

from .providers import digest

VERSION = 2
FPS = 30
EXCERPT_GAP = 15


class VisualConstraintError(ValueError):
    retryable = False

    def __init__(self, code, detail, context=None):
        self.code, self.detail = code, detail
        self.context = dict(context or {})
        super().__init__(f'[visual:{code}] {detail} Không thể tự cân thêm trong giới hạn an toàn; '
                         'tăng khoảng thời lượng cảnh hoặc chọn Tự động rồi chạy lại.')


def active(settings):
    return (settings.get('editorial_mode') == 'reaction_cops'
            and settings.get('reaction_scene_duration_mode') == 'range')


def bounds(settings):
    lo = math.ceil(float(settings.get('reaction_scene_min_seconds', 10))*FPS-1e-8)
    hi = math.floor(float(settings.get('reaction_scene_max_seconds', 20))*FPS+1e-8)
    if not FPS <= lo <= hi <= 25*FPS:
        raise VisualConstraintError('bounds', 'Khoảng cảnh không chứa thời lượng hợp lệ ở 30 fps.')
    return lo, hi


def shots(project):
    result = []
    previous = 0
    duration = round(project['metadata']['duration']*FPS)
    for i, row in enumerate(project.get('shots', [])):
        a, b = round(row['start']*FPS), round(row['end']*FPS)
        if a != previous or b <= a or b > duration+1:
            raise VisualConstraintError('shot_index', 'Chỉ mục chuyển cảnh nguồn bị thiếu hoặc không hợp lệ.')
        result.append(dict(id=f'shot{i}', a=a, b=min(b, duration)))
        previous = b
    if not result or abs(previous-duration) > 1:
        raise VisualConstraintError('shot_index', 'Cần lập chỉ mục chuyển cảnh thật của nguồn trước khi dựng.')
    return result


def signature(project, logical):
    return digest(dict(version=VERSION, bounds=bounds(project['settings']),
                       source=project.get('source'), duration=project['metadata']['duration'],
                       shots=project.get('shots'), plan=project['story_plan'],
                       cues=project.get('reaction_cues'), speech=project.get('source_speech'),
                       clips=logical))


def _merge(ranges):
    merged = []
    for a, b in sorted(ranges):
        if b <= a:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged


def _windows(project, logical):
    """Union logical clips belonging to the same commentary or dialogue window."""
    from .reaction_commentary import members
    plan = project['story_plan']
    rows = plan['selections']
    parent = list(range(len(rows)))
    def root(i):
        while parent[i] != i:
            i = parent[i]
        return i
    def join(indices):
        indices = list(indices)
        if not indices:
            return
        for i in indices[1:]:
            parent[root(i)] = root(indices[0])
    for i, row in enumerate(rows):
        if row.get('narration', '').strip():
            join(range(i, i+len(members(plan, i))))
    index = {r['id']: i for i, r in enumerate(rows)}
    for w in plan.get('dialogue_dubs', []):
        join(index[sid] for sid in w.get('selection_ids', []) if sid in index)
    # Commentary/dub spans are already validated by the locked plan contract.
    clips = {c['segment_id']: c for c in logical if c['kind'] != 'hook'}
    groups = []
    for i, row in enumerate(rows):
        key = root(i)
        if groups and groups[-1][0] == key:
            groups[-1][1].append(row)
        else:
            groups.append((key, [row]))
    return [(group, clips[group[0]['id']]['start'], clips[group[-1]['id']]['end'])
            for _, group in groups]


def _beats(project, rows):
    """Evidence changes form ordered beats; artificial cue subdivisions do not."""
    from .reaction_cops import _evidence_ids
    beats = []
    for row in rows:
        ids = set(_evidence_ids(row.get('evidence', '')))
        if not ids:
            raise VisualConstraintError('evidence', f"{row['id']} thiếu cue dẫn chứng.")
        if beats and ids & beats[-1]['ids']:
            beats[-1]['ids'].update(ids)
            beats[-1]['rows'].append(row)
        else:
            beats.append(dict(ids=ids, rows=[row]))
    return beats


def _transition(previous, candidate, length):
    if previous is None:
        return True
    if candidate['a'] < previous['end']:
        return False
    if candidate['shot_id'] != previous['shot_id']:
        return True
    # Continuous bodycam footage may contain distinct developments. A new
    # evidenced excerpt is allowed only after an actual omitted source gap;
    # adjacent halves or repeated excerpts of the same cited speech are not.
    return (candidate['a'] - previous['end'] >= EXCERPT_GAP and
            set(_ids(candidate, length)).isdisjoint(previous['evidence_ids']))


def _previous(candidate, length):
    return dict(end=candidate['a']+length, shot_id=candidate['shot_id'],
                evidence_ids=_ids(candidate, length))


def _candidates(project, rows, source_shots, low, high, source_floor, last_shot):
    cues = project.get('reaction_cues', [])
    roles = project.get('source_speech', {}).get('items', [])
    verified = {c['id']: c for c, role in zip(cues, roles)
                if role.get('role') == 'participant' and role.get('confidence', 0) >= .7}
    excluded = [(math.floor(c['start']*FPS), math.ceil(c['end']*FPS))
                for c in cues if c['id'] not in verified]
    beats = _beats(project, rows)
    groups = {}
    for beat_index, beat in enumerate(beats):
        if not beat['ids'] <= verified.keys():
            raise VisualConstraintError('evidence', 'Cảnh hình chỉ được dùng dẫn chứng hiện trường đã xác minh.')
        # Expanding to whole *cited* cues recovers footage lost by partial logical
        # splits. Never borrow an unrelated chapter, narrator, or uncertain cue.
        ranges = [(round(r['start']*FPS), round(r['end']*FPS)) for r in beat['rows']]
        ranges += [(math.ceil(verified[i]['start']*FPS), math.floor(verified[i]['end']*FPS))
                   for i in beat['ids']]
        # Expand only already cited verified cues. Temporal proximity alone
        # does not prove an uncited cue belongs to the same person/incident.
        allowed = _merge(ranges)
        for left, right in excluded:
            allowed = [(a, b) for x, y in allowed
                       for a, b in ((x, min(y, left)), (max(x, right), y)) if b > a]
        for shot in source_shots:
            for a, b in allowed:
                a, b = max(a, shot['a'], source_floor), min(b, shot['b'])
                if b-a < low:
                    continue
                evidence = [verified[i] for i in sorted(beat['ids'])
                            if verified[i]['end']*FPS > a and verified[i]['start']*FPS < b]
                if not evidence:
                    continue
                # Select around a cited action/utterance, not around an arbitrary
                # equal subdivision. Fixed anchor ensures truncation remains cited.
                # Each real utterance supplies one evidence anchor, never an
                # arbitrary grid. Also retain the first allowed start, so an
                # anchor does not waste verified footage before the cue.
                starts = {a, max(a, min(b-low, round(evidence[0]['start']*FPS)))}
                starts.update(round(c['start']*FPS) for c in evidence if a <= c['start']*FPS <= b-low)
                for start in sorted(starts):
                    cap = min(high, b-start)
                    candidate = dict(shot_id=shot['id'], a=start, cap=cap, beat=beat_index,
                        cue_spans=[(c['id'], c['start']*FPS, c['end']*FPS) for c in evidence],
                        segment_ids=[r['id'] for r in beat['rows']],
                        reason=beat['rows'][0].get('reason', 'Verified event evidence'))
                    candidate['previous_window'] = last_shot
                    groups.setdefault((shot['id'], start), []).append(candidate)
    return beats, [groups[k] for k in sorted(groups, key=lambda k:k[1])]


def _ids(candidate, length):
    if 'ids_by_length' in candidate:
        return candidate['ids_by_length'].get(length, [])
    a, b = candidate['a'], candidate['a']+length
    return [identifier for identifier, left, right in candidate['cue_spans']
            if min(b, right)-max(a, left) >= min(15, right-left)-1e-6]


def _solve(groups, total, low, high, beat_count, check):
    """Ordered bounded knapsack: distinct evidenced excerpts, exact frames.

    A reading window is <=25s, so the frame state remains small. Keeping the
    best path for (frames, evidence prefix) bounds work without AI retry loops.
    """
    incompatible = total < low or math.ceil(total/high) > total//low
    states = {(0, 0, None): ((-1, 0.0), ())}
    for group in groups:
        check()
        current_shot = group[0]['shot_id']
        # Once source time enters a new physical shot, all older-shot endings
        # have identical transition permissions. Keep their earliest path,
        # rather than multiplying states by all 600 shots of a long review.
        compact = {}
        for (used, covered, ending), value in states.items():
            key = (used, covered, ending if ending and ending[0] == current_shot else None)
            if key not in compact or value[0] < compact[key][0]:
                compact[key] = value
        states = compact
        for candidate in group:
            candidate.pop('ids_by_length', None)
            candidate['ids_by_length'] = {n:_ids(candidate,n)
                for n in range(low,min(candidate['cap'],total)+1)}
        updated = dict(states)
        for (used, covered, _), (cost, path) in states.items():
            for candidate in group:
                beat = candidate['beat']
                if beat not in (covered, covered-1):
                    continue
                for length in range(low, min(candidate['cap'], total-used)+1):
                    if not _ids(candidate, length):
                        continue
                    previous = _previous(*path[-1]) if path else candidate.get('previous_window')
                    if not _transition(previous, candidate, length):
                        continue
                    key = (used+length, max(covered, beat+1),
                           (candidate['shot_id'], tuple(_ids(candidate, length))))
                    # Finish as early in the source as possible. Optimizing only
                    # cut count can consume later shots needed by the next voice
                    # window even though an earlier complete plan was feasible.
                    value = (candidate['a']+length,
                             cost[1] + .2 + abs(length-(low+high)/2)*.0001)
                    if key not in updated or value < updated[key][0]:
                        updated[key] = value, path+((candidate, length),)
        states = updated
    result = min((value for (frames, covered, _), value in states.items()
                  if frames == total and covered == beat_count), key=lambda value:value[0], default=None)
    if result is None:
        capacities = [max(c['cap'] for c in g) for g in groups]
        upper_capacity = sum(capacities)
        feasible = max((frames for frames, covered, _ in states if covered == beat_count), default=0)
        context = {'required_frames': total, 'capacity_frames': feasible,
                   'candidate_capacity_frames': upper_capacity,
                   'feasible_frames': feasible,
                   'required_shots': math.ceil(total/high), 'candidate_shots': len(groups),
                   'physical_shots': len({c['shot_id'] for g in groups for c in g}),
                   'capacities': capacities}
        if incompatible:
            raise VisualConstraintError('frame_budget',
                f'Cửa sổ {total/FPS:.2f}s không chia hết thành cảnh {low/FPS:g}–{high/FPS:g}s.', context)
        raise VisualConstraintError('insufficient_distinct_shots',
            f'Cửa sổ cần {total/FPS:.2f}s / ít nhất {math.ceil(total/high)} cảnh khác nhau; '
            f'có {len(groups)} đoạn dẫn chứng phù hợp, lập được tối đa {feasible/FPS:.2f}s theo setting. '
            'Không chia một cảnh nguồn thành A/B hoặc lấy cảnh khác tình huống để bù.',
            context)
    return result[1]


def plan(project, logical, check=lambda: None, *, window_cache=None):
    started = time.monotonic()
    low, high = bounds(project['settings'])
    source_shots = shots(project)
    cuts = [copy.deepcopy(c) for c in logical if c['kind'] == 'hook']
    floor, last_shot = 0, None
    prior_floor, prior_excerpt = 0, None
    visual_beats = []
    for rows, start, end in _windows(project, logical):
        # Recovery changes only a few windows. Memoize successful prefix solves
        # within this single transaction, never across projects/source changes.
        key=digest(dict(rows=rows,low=low,high=high,floor=floor,last=last_shot)) if window_cache is not None else None
        cached=window_cache.get(key) if window_cache is not None else None
        if cached:
            beats,path=cached
            groups=None
        else:
            beats,groups=_candidates(project,rows,source_shots,low,high,floor,last_shot)
        try:
            if not cached:
                path = _solve(groups, round((end-start)*FPS), low, high, len(beats), check)
                if window_cache is not None:
                    window_cache[key]=(beats,path)
        except VisualConstraintError as exc:
            context = dict(exc.context)
            context.update(window_id=rows[0]['id'], window_start=start,
                           window_end=end,
                           source_floor=floor, previous_excerpt=copy.deepcopy(last_shot),
                           prior_window_floor=prior_floor, prior_window_excerpt=copy.deepcopy(prior_excerpt),
                           row_ids=[r['id'] for r in rows])
            raise VisualConstraintError(exc.code, f"{rows[0]['id']} · {start:.2f}–{end:.2f}s: {exc.detail}", context) from None
        cursor = round(start*FPS)
        for candidate, length in path:
            a = candidate['a']
            cuts.append(dict(id=f'visual{len(cuts)}', kind='highlight',
                             source_start=a/FPS, source_end=(a+length)/FPS,
                             start=cursor/FPS, end=(cursor+length)/FPS,
                             part=rows[0]['part'], segment_id=rows[0]['id'],
                             section=rows[0]['section'], window_id=rows[0]['id'],
                             shot_id=candidate['shot_id'], evidence_ids=_ids(candidate, length),
                             evidence_segments=candidate['segment_ids'], reason=candidate['reason']))
            cursor += length
        prior_floor, prior_excerpt = floor, copy.deepcopy(last_shot)
        floor = path[-1][0]['a']+path[-1][1]
        last_shot = _previous(*path[-1])
        visual_beats.append(dict(window_id=rows[0]['id'], start=start, end=end,
                                 text=' '.join(r.get('narration', '') for r in rows).strip(),
                                 evidence_ids=sorted(set(i for beat in beats for i in beat['ids'])),
                                 cuts=[c['id'] for c in cuts if c.get('window_id') == rows[0]['id']]))
    manifest = dict(version=VERSION, status='ready', fingerprint=signature(project, logical),
                    clips=cuts, beats=visual_beats, cut_count=len(cuts),
                    regular_cut_count=sum(c['kind'] != 'hook' for c in cuts),
                    min_seconds=low/FPS, max_seconds=high/FPS)
    validate(project, logical, manifest)
    manifest['planning_seconds'] = round(time.monotonic()-started, 3)
    return manifest


def validate(project, logical, manifest):
    low, high = bounds(project['settings'])
    indexed = {s['id']: s for s in shots(project)}
    cursor, source_end, last = 0, 0, None
    if manifest.get('version') != VERSION or manifest.get('fingerprint') != signature(project, logical):
        raise VisualConstraintError('stale', 'Lịch hình không khớp nguồn, dẫn chứng hoặc setting hiện tại.')
    for c in manifest.get('clips', []):
        a, b = round(c['source_start']*FPS), round(c['source_end']*FPS)
        x, y = round(c['start']*FPS), round(c['end']*FPS)
        if x != cursor or b-a != y-x or b <= a:
            raise VisualConstraintError('continuity', 'Lịch hình có khoảng trống hoặc lệch số frame.')
        cursor = y
        if c['kind'] == 'hook':
            hook = next((r for r in logical if r['kind'] == 'hook'), None)
            if not hook or any(c[k] != hook[k] for k in ('start','end','source_start','source_end')):
                raise VisualConstraintError('hook', 'Hook phải giữ lịch riêng đã khóa.')
            continue
        shot = indexed.get(c.get('shot_id'))
        if not shot or not shot['a'] <= a < b <= shot['b'] or not low <= y-x <= high:
            raise VisualConstraintError('cut_bounds', 'Cảnh không thuộc một shot thật hoặc vượt khoảng thời lượng.')
        candidate = dict(a=a, shot_id=c['shot_id'],
                         cue_spans=[(i,a,b) for i in c.get('evidence_ids',[])])
        if a < source_end or not _transition(last, candidate, b-a):
            raise VisualConstraintError('same_shot', 'Hai cảnh liên tiếp là chia đôi cùng cảnh nguồn hoặc đảo/lặp nguồn.')
        if not c.get('evidence_ids'):
            raise VisualConstraintError('evidence', 'Cảnh thiếu dẫn chứng.')
        source_end, last = b, dict(end=b, shot_id=c['shot_id'], evidence_ids=c['evidence_ids'])
    if cursor != round(logical[-1]['end']*FPS):
        raise VisualConstraintError('duration', 'Tổng lịch hình phải bằng lịch thoại đã khóa.')
    floor, last = 0, None
    assigned = set()
    for rows, start, end in _windows(project, logical):
        group = [c for c in manifest['clips'] if c.get('window_id') == rows[0]['id']]
        if not group or group[0]['start'] != start or group[-1]['end'] != end:
            raise VisualConstraintError('window', 'Cảnh hình vượt cửa sổ thoại/dẫn chứng đã khóa.')
        beats, candidates = _candidates(project, rows, list(indexed.values()), low, high, floor, last)
        covered = set()
        for cut in group:
            a, length = round(cut['source_start']*FPS), round((cut['end']-cut['start'])*FPS)
            match = next((c for choices in candidates for c in choices
                          if c['shot_id'] == cut['shot_id'] and c['a'] == a and length <= c['cap']
                          and _ids(c, length) == cut['evidence_ids']
                          and c['segment_ids'] == cut['evidence_segments']), None)
            if not match or cut['part'] != rows[0]['part']:
                raise VisualConstraintError('evidence', 'Cảnh hình không thuộc dẫn chứng đúng cửa sổ/tình huống.')
            covered.add(match['beat'])
            assigned.add(cut['id'])
        if covered != set(range(len(beats))):
            raise VisualConstraintError('beat_coverage', 'Lịch hình bỏ mất một diễn biến dẫn chứng trong lời kể.')
        floor = round(group[-1]['source_end']*FPS)
        last = dict(end=floor, shot_id=group[-1]['shot_id'], evidence_ids=group[-1]['evidence_ids'])
    if len(assigned) != sum(c['kind'] != 'hook' for c in manifest['clips']):
        raise VisualConstraintError('window', 'Có cảnh ngoài các cửa sổ dẫn chứng đã khóa.')


def apply(project, timeline, strict=False, check=lambda: None):
    if not active(project['settings']) or not timeline.get('planned'):
        return timeline
    logical = timeline.get('audio_clips', timeline['clips'])
    cached = project.get('visual_edit') or {}
    try:
        fingerprint = signature(project, logical)
        if cached.get('status') == 'blocked' and cached.get('fingerprint') == fingerprint:
            raise VisualConstraintError(cached['code'], cached['detail'], cached.get('context'))
        if cached.get('status') == 'ready' and cached.get('fingerprint') == fingerprint:
            # Exact generated manifests are immutable to API clients; avoid
            # repeating the solver during polling/preview.
            manifest = cached
            validate(project, logical, manifest)
        else:
            manifest = plan(project, logical, check)
    except VisualConstraintError as exc:
        project['visual_edit'] = dict(version=VERSION, status='blocked', code=exc.code,
                                      detail=exc.detail, message=str(exc),
                                      context=copy.deepcopy(exc.context),
                                      fingerprint=signature(project, logical))
        if strict:
            raise
        timeline['visual_edit'] = dict(status='blocked', code=exc.code, message=str(exc))
        timeline['warnings'].insert(0, str(exc)+' Preview đang dùng lịch nguồn, chưa phải bản xuất theo khoảng.')
        return timeline
    project['visual_edit'] = manifest
    project['warnings'] = [w for w in project.get('warnings', [])
                           if not (w.startswith('Reaction COPS:') and 'cảnh ngoài khoảng' in w)]
    timeline['warnings'] = [w for w in timeline['warnings']
                            if not (w.startswith('Reaction COPS:') and 'cảnh ngoài khoảng' in w)]
    timeline.update(audio_clips=copy.deepcopy(logical), clips=copy.deepcopy(manifest['clips']),
                    visual_edit={k: v for k, v in manifest.items() if k != 'clips'})
    return timeline


def prepare(project, report, check):
    if not active(project['settings']) or not project.get('story_plan'):
        return project
    from . import media, store
    from .timeline import build_story
    from .story import plan_is_current
    if not plan_is_current(project):
        return project
    report(0, 'Lập lịch hình từ các cảnh nguồn khác nhau; giữ lịch thoại đã khóa…')
    media.ensure_shots(project, report, check)
    # No TTS readiness required: test visual feasibility before spending time
    # generating voices. Export performs its usual strict audio validation.
    timeline = build_story(project, visual=False)
    try:
        apply(project, timeline, strict=True, check=check)
    except VisualConstraintError:
        store.save(project)
        raise
    report(5, f"Lịch hình đã đạt · {project['visual_edit']['cut_count']} cảnh · không chia đôi cùng shot.")
    return store.save(project)
