"""Optional cue-complete scene lengths; automatic mode retains the existing edit."""
import re


def limits(settings):
    if (settings.get('editorial_mode') != 'reaction_cops' or
            settings.get('reaction_scene_duration_mode') != 'range'):
        return None
    return (settings.get('reaction_scene_min_seconds', 10),
            settings.get('reaction_scene_max_seconds', 20))


def instructions(settings):
    bounds = limits(settings)
    if not bounds:
        return ''
    low, high = bounds
    return (f'\nVISUAL CUT DURATION: the final independent picture edit requires {low:g}–{high:g} '
            'seconds per real source shot, excluding the separately configured hook. '
            'Do not fake cuts by dividing one continuous shot into adjacent pieces. '
            'The selections below are logical evidence/audio ranges, not final picture cuts. '
            'Evidence and complete exchanges take '
            'priority, then chapter coverage and total duration, then scene length. Select events '
            'first and adjust boundaries only on complete source cues. Keep a question with its '
            'answer when possible. Never cross excluded narration, uncertain speech or a new '
            'incident to fill a scene; never repeat footage, pad silence or stretch voice. '
            'Do not add commentary when splitting footage. Keep the requested commentary count '
            'A commentary window may span multiple neighbouring output scenes: the scene range '
            'is NOT the voice word budget. Reserve enough clean evidenced footage for natural '
            'speech across those cuts, without adding excluded source gaps to the output. '
            'and existing two-sentence style; 11–16 seconds of voice remains a reference, not '
            'the length of every scene. Logical whole-cue ranges may be shorter or longer; '
            'the final picture edit has no whole-cue duration exceptions. '
            'A narrated selection must still fit the existing 25-second technical ceiling.\n')


def _continues(text):
    # A question and the next response form one exchange. Preserve explicit
    # unfinished clauses too; this is a boundary hint, not speaker inference.
    return bool(re.search(r'[?,:;\-–—]\s*["\u201d\u2019]*$', text.strip()))


def _partition(rows, low, high, anchors):
    """Bounded dynamic programming avoids a tiny tail after greedy cuts."""
    units = []
    for row in rows:
        if units and (row['start'] < max(c['end'] for c in units[-1]) - .04 or
                      _continues(units[-1][-1]['text'])):
            units[-1].append(row)
        else:
            units.append([row])
    count = len(units)
    costs = [float('inf')] * count + [0.0]
    next_cut = [0] * count
    ends = [max(c['end'] for c in unit) for unit in units]
    center = (low + high) / 2
    for i in range(count - 1, -1, -1):
        start = units[i][0]['start']
        end = start
        for j in range(i, count):
            # A known AI-selected turning point must not disappear merely
            # because adjacent footage fits a longer preferred scene.
            if j > i and any(c['id'] in anchors for c in units[j]):
                break
            end = max(end, ends[j])
            length = end - start
            deviation = max(0, low - length, length - high)
            cost = deviation * 10 + .1 + abs(length - center) * .005 + costs[j + 1]
            # Prefer a completed sentence over a boundary without punctuation.
            if j + 1 < count and not re.search(r'[.!…]["\u201d\u2019]*$', units[j][-1]['text'].strip()):
                cost += .05
            if cost < costs[i]:
                costs[i], next_cut[i] = cost, j + 1
            # One whole unit beyond max is enough to consider a natural exception.
            if length > high:
                break
    blocks = []
    i = 0
    while i < count:
        j = next_cut[i]
        cues = [c for unit in units[i:j] for c in unit]
        blocks.append({'start': cues[0]['start'], 'end': max(c['end'] for c in cues),
                       'source_cue_ids': [c['id'] for c in cues],
                       'priority': max(c['priority'] for c in cues)})
        i = j
    return blocks


def clean_blocks(project, anchor_ids=()):
    """Only verified clean cues, with <=1.5s context gaps and no excluded speech."""
    from .source_speech import allowed
    low, high = limits(project['settings'])
    if project.get('transcript_origin') == 'youtube_auto_subtitles':
        # Display updates can overlap throughout an entire conversation. Do
        # not mistake that chain for one indivisible spoken turn. Reuse the
        # existing rolling-caption mapping, retaining complete cited cues and
        # its non-overlap/exclusion checks, with the requested maximum.
        from .reaction_cops import _clean_footage
        fallback = {**project, 'settings': {**project['settings'],
                                          'reaction_scene_duration_mode': 'auto'}}
        return _clean_footage(fallback, max_seconds=high)
    roles = project.get('source_speech', {}).get('items', [])
    excluded = [r for r in roles if r['role'] != 'participant' or r['confidence'] < .7]
    blocked = lambda start, end: any(r['end'] > start + .04 and r['start'] < end - .04
                                     for r in excluded)
    paired = zip(project.get('reaction_cues', []),
                 roles)
    rows = sorted(({**cue, 'priority': role['priority']} for cue, role in paired
                   if role['role'] == 'participant' and role['confidence'] >= .7
                   and not blocked(cue['start'], cue['end'])), key=lambda c: (c['start'], c['end']))
    groups = []
    current = []
    end = 0
    for row in rows:
        proposed_end = max(end, row['end'])
        if current and (row['start'] > end + 1.5 or
                        blocked(current[0]['start'], proposed_end)):
            groups.append(current)
            current = []
        current.append(row)
        end = max(c['end'] for c in current)
    if current:
        groups.append(current)
    anchors = set(anchor_ids)
    return [block for group in groups for block in _partition(group, low, high, anchors)
            if block['end'] - block['start'] >= 1 and allowed(project, block['start'], block['end'])]


def audit(selections, settings):
    bounds = limits(settings)
    if not bounds:
        return None
    low, high = bounds
    exceptions = []
    for item in selections:
        length = item['end'] - item['start']
        if length < low - .05 or length > high + .05:
            exceptions.append({'selection_id': item['id'], 'source_start': item['start'],
                               'source_end': item['end'], 'seconds': round(length, 3),
                               'reason': 'short' if length < low else 'long'})
    return {'mode': 'range', 'min_seconds': low, 'max_seconds': high,
            'scene_count': len(selections), 'within_range': len(selections) - len(exceptions),
            'exceptions': exceptions}
