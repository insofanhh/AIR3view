"""Rebuild karaoke timing from existing audio without resynthesizing voices."""
import json
from . import store
from .alignment import align_script
from .media import transcribe
from .providers import voice_hash, digest


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
    voiced = [n for n in project['narrations'] if n.get('audio') and n.get('audio_hash') == voice_hash(n, settings)]
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
    if project.get('transcript') and project['metadata'].get('has_audio'):
        report(40, 'Canh từng từ thoại gốc từ âm thanh…')
        source_audio = folder/'audio.wav'
        cache = folder/'word-alignment'
        cache.mkdir(exist_ok=True)
        fingerprint = digest({'audio': [source_audio.stat().st_size, source_audio.stat().st_mtime_ns], 'asr': settings['asr_model'], 'version':1})
        path = cache/(fingerprint+'.json')
        if path.exists():
            recognized = json.loads(path.read_text('utf-8'))
        else:
            recognized = transcribe(source_audio, settings, check)
            path.write_text(json.dumps(recognized,ensure_ascii=False),'utf-8')
        check()
        observed = [w for c in recognized for w in c.get('words',[])]
        project['transcript'] = align_existing(project['transcript'], observed)
    missing = sum(not c.get('words') for c in project['transcript'])
    project['warnings'] = [w for w in project.get('warnings',[]) if not w.startswith('Karaoke:')]
    if missing:
        project['warnings'].append(f'Karaoke: {missing} câu chưa có mốc từ đủ tin cậy; các câu này giữ phụ đề thường. Bản dịch không dùng mốc từ của ngôn ngữ nguồn.')
    project.update(exports=[], preview_exports=[])
    report(100, 'Đã canh phụ đề theo từng từ')
    return store.save(project)
