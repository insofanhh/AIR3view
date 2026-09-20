"""Align known narration text to ASR timing anchors without replacing its words."""
import difflib
import re
import unicodedata


def normalize(word):
    word = unicodedata.normalize('NFKD', word.lower().replace('đ', 'd'))
    return re.sub(r'[^a-z0-9]', '', ''.join(c for c in word if not unicodedata.combining(c)))


def align_script(text, words, duration):
    expected = text.split()
    if not expected:
        return []
    observed = []
    for w in words:
        tokens = w['text'].split()
        for i, token in enumerate(tokens):
            step = (w['end'] - w['start']) / max(1, len(tokens))
            observed.append({'text': token, 'start': w['start'] + i * step, 'end': w['start'] + (i+1) * step})
    matcher = difflib.SequenceMatcher(None, [normalize(w) for w in expected], [normalize(w['text']) for w in observed], autojunk=False)
    matches = sum(block.size for block in matcher.get_matching_blocks())
    if not observed or matches / len(expected) < .45:
        # Weak evidence: keep one explicitly utterance-level cue, never fake word timing.
        return [{'id': 'c0', 'start': 0, 'end': duration, 'text': text, 'speaker': 'ai'}]
    aligned = []
    for tag, a, b, c, d in matcher.get_opcodes():
        if a == b:
            continue
        if tag == 'equal':
            for i, j in zip(range(a, b), range(c, d)):
                aligned.append({'text': expected[i], 'start': observed[j]['start'], 'end': observed[j]['end']})
        else:
            left = observed[c]['start'] if c < d else (observed[c-1]['end'] if c else 0)
            right = observed[d-1]['end'] if c < d else (observed[c]['start'] if c < len(observed) else duration)
            right = max(left, right)
            weights = [max(1, len(expected[i])) for i in range(a, b)]
            total = sum(weights)
            cursor = left
            for i, weight in zip(range(a,b), weights):
                end = cursor + (right - left) * weight / total
                aligned.append({'text': expected[i], 'start': cursor, 'end': end})
                cursor = end
    cues, group = [], []
    def flush():
        if group:
            start = max(0, min(duration, group[0]['start']))
            end = max(start, min(duration, group[-1]['end']))
            if end > start:
                cues.append({'id': 'c'+str(len(cues)), 'start': start, 'end': end, 'text': ' '.join(w['text'] for w in group), 'speaker': 'ai', 'words': [{'text':w['text'], 'start':max(start,min(end,w['start'])), 'end':max(start,min(end,w['end']))} for w in group]})
            elif cues:
                cues[-1]['text'] += ' ' + ' '.join(w['text'] for w in group)
                cues[-1]['words'] = []
    for word in aligned:
        if group and (len(group) >= 8 or word['start'] - group[-1]['end'] > .65 or word['end'] - group[0]['start'] > 4.5):
            flush()
            group = []
        group.append(word)
    flush()
    # All expected text must survive alignment, including zero-duration missing prefixes.
    if ' '.join(c['text'] for c in cues) != ' '.join(expected):
        return [{'id':'c0','start':0,'end':duration,'text':text,'speaker':'ai'}]
    return cues
