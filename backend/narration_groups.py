"""Join adjacent narration fragments without moving source or dialogue cuts."""
import copy


def join_short_slots(selections, eligible=None):
    groups = [(copy.deepcopy(item), [index]) for index, item in enumerate(selections)]
    while True:
        merged = False
        for i, (item, members) in enumerate(groups):
            if not item['narration'].strip() or item['end']-item['start'] >= 4-.001:
                continue
            if eligible is not None and not any(index in eligible for index in members):
                continue
            candidates = []
            for j in (i-1, i+1):
                if not 0 <= j < len(groups):
                    continue
                other, other_members = groups[j]
                left, right = (other, item) if j < i else (item, other)
                if (not other['narration'].strip() or left['part'] != right['part']
                        or abs(left['end']-right['start']) > .001
                        or right['end']-left['start'] > 25
                        or left.get('narration_offset', 0) != 0 or right.get('narration_offset', 0) != 0):
                    continue
                # Prefer an unfinished neighbour to preserve completed audio.
                candidates.append((0 if eligible is None or any(k in eligible for k in other_members) else 1, j))
            if not candidates:
                continue
            j = min(candidates)[1]
            a, b = sorted((i, j))
            left, lm = groups[a]
            right, rm = groups[b]
            joined = {**left, 'end': right['end'],
                      'section': 'ending' if right['section']=='ending' else left['section'],
                      'narration': left['narration'].strip()+' '+right['narration'].strip(),
                      'evidence': left['evidence']+'\n'+right['evidence']}
            groups[a:b+1] = [(joined, lm+rm)]
            merged = True
            break
        if not merged:
            return groups


def repair_existing(project, report, check):
    from . import store
    from .story import plan_is_current, validate_plan, storytelling
    from .providers import voice_hash
    if not storytelling(project['settings']) or not plan_is_current(project):
        return project
    # Work on a copy; failed validation must never publish a partial edit.
    edited = copy.deepcopy(project)
    slots = edited['story_plan']['selections']
    by_segment = {n.get('segment_id'): n for n in edited['narrations']}
    eligible = set()
    for i, slot in enumerate(slots):
        n = by_segment.get(slot.get('id'))
        if slot['narration'].strip():
            if not n or not n['enabled'] or abs(n['start']-slot['start']) > .05:
                return project  # Preserve manual exclusions and moved narration.
            slot['narration'] = n['text']  # Preserve user's edited prose.
            if not (n.get('audio_hash') == voice_hash(n, edited['settings'])
                    and n.get('duration', 0) > 0 and n.get('audio')
                    and store.asset(project['id'], n['audio']).is_file()):
                eligible.add(i)
    groups = join_short_slots(slots, eligible)
    if len(groups) == len(slots):
        return project
    edited['story_plan']['selections'] = [item for item, _ in groups]
    validated = validate_plan(edited['story_plan'], edited, check_text=False)
    # validate_plan assigns sequential segment IDs; rebind existing audio to
    # those new IDs while keeping unchanged narration IDs/hashes/files.
    narrations = [copy.deepcopy(n) for n in edited['narrations'] if n.get('segment_id')=='hook']
    for item, (_, indices) in zip(validated['selections'], groups):
        if not item['narration'].strip():
            continue
        n = copy.deepcopy(by_segment[slots[indices[0]]['id']])
        n['segment_id'] = item['id']
        if len(indices) > 1:
            n.update(text=item['narration'], start=item['start'], section=item['section'],
                     evidence=item['evidence'], target_duration=round(item['end']-item['start']-.04, 3),
                     audio='', audio_hash='', duration=0, cues=[], caption_version=0)
        narrations.append(n)
    edited.update(story_plan=validated, narrations=narrations, exports=[], preview_exports=[])
    check()
    report(2, 'Gộp lời kể quá ngắn vào cảnh AI liền kề; giữ thời lượng và thoại gốc…')
    return store.save(edited)
