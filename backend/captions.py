"""Rebuild karaoke timing from existing audio without resynthesizing voices."""
from . import store
from .alignment import align_script
from .media import transcribe
from .providers import voice_hash


def align_existing(cues, observed):
    """Keep supplied caption text and sentence boundaries; use ASR word anchors."""
    cursor = 0
    result = []
    for cue in cues:
        while cursor < len(observed) and observed[cursor]['end'] <= cue['start']:
            cursor += 1
        words = []
        i = cursor
        while i < len(observed) and observed[i]['start'] < cue['end']:
            w = observed[i]
            a, b = max(cue['start'], w['start']), min(cue['end'], w['end'])
            if b > a:
                words.append({'text':w['text'], 'start':a-cue['start'], 'end':b-cue['start']})
            i += 1
        aligned = align_script(cue['text'], words, cue['end']-cue['start'])
        timings = [w for c in aligned for w in c.get('words', [])]
        if ' '.join(w['text'] for w in timings).split() != cue['text'].split():
            timings = []
        result.append({**cue, 'words': [{'text':w['text'], 'start':w['start']+cue['start'], 'end':w['end']+cue['start']} for w in timings]})
    return result


def refresh(project, report, check):
    folder = store.project_dir(project['id'])
    settings = project['settings']
    voiced = [n for n in project['narrations'] if n.get('enabled',True) and n.get('audio') and n.get('audio_hash') == voice_hash(n, settings)]
    for i, n in enumerate(voiced):
        check()
        if n.get('caption_version') == 4:
            continue
        report(5 + 30*i/max(1,len(voiced)), f"Canh từng từ giọng AI {i+1}/{len(voiced)}…")
        audio = store.asset(project['id'], n['audio'])
        # Rebuild readable phrase boundaries too: older alignment may have
        # fallen back to one paragraph spanning the entire narration.
        n['cues'] = transcribe(audio, settings, check, expected_text=n['text'])
        for c in n['cues']:
            c['speaker'] = 'ai'
        n['caption_version'] = 4
        project.update(exports=[], preview_exports=[])
        store.save(project)
    from .source_captions import refresh_source
    stats, missing_indices = refresh_source(project, report, check, transcribe, align_existing)
    stats['plain']=len(missing_indices)
    project['source_caption_stats'] = stats
    missing = len(missing_indices)
    project['warnings'] = [w for w in project.get('warnings',[]) if not w.startswith('Karaoke:')]
    if missing:
        project['warnings'].append(f'Karaoke: {missing} câu thoại gốc được dùng chưa có mốc từ đủ tin cậy; các câu này giữ phụ đề thường. Bản dịch không dùng mốc từ của ngôn ngữ nguồn.')
    project.update(exports=[], preview_exports=[])
    report(100, 'Đã canh phụ đề theo từng từ')
    return store.save(project)
