"""Align only audible source dialogue, with durable per-cue results."""
import json
import math
import re
from . import store
from .providers import digest


def valid_words(cue):
    words=cue.get('words') or []
    try:
        return (bool(words) and ' '.join(w['text'] for w in words).split()==cue['text'].split()
                and all(math.isfinite(w['start']) and math.isfinite(w['end'])
                        and cue['start']-.001<=w['start']<=w['end']<=cue['end']+.001 for w in words)
                and all(a['end']<=b['start']+.001 for a,b in zip(words,words[1:])))
    except (KeyError,TypeError):
        return False


def source_windows(project):
    """Map actually visible original captions back to source time coordinates."""
    from .timeline import build
    t=build(project)
    ranges=[]
    for cue in t['cues']:
        if cue.get('speaker')!='original':continue
        for clip in t['clips']:
            if clip['kind']=='freeze':continue
            a,b=max(cue['start'],clip['start']),min(cue['end'],clip['end'])
            if b>a:
                ranges.append((clip['source_start']+a-clip['start'],clip['source_start']+b-clip['start']))
    return merge_windows(ranges)


def merge_windows(ranges,gap=0):
    merged=[]
    for a,b in sorted(ranges):
        if merged and a<=merged[-1][1]+gap:
            merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
        else:merged.append((a,b))
    return merged


def refresh_source(project,report,check,transcribe,align_existing):
    from .media import run,FFMPEG
    cues=project.get('transcript',[])
    settings=project['settings']
    stats={'selected_cues':0,'reused':0,'transcribed_seconds':0,'asr_calls':0,'plain':0}
    if not cues or not project.get('metadata',{}).get('has_audio'):
        return stats,[]
    # Common case: no work and no filesystem/model access at all.
    if all(valid_words(c) for c in cues):
        stats['reused']=len(cues)
        return stats,[]
    windows=source_windows(project)
    selected=[i for i,c in enumerate(cues) if any(c['start']<b and c['end']>a for a,b in windows)]
    stats['selected_cues']=len(selected)
    if not selected:return stats,[]
    source_by_id={c['id']:c for c in project.get('source_transcript',[])}
    pending=[]
    for i in selected:
        check()
        cue=cues[i]
        if valid_words(cue):stats['reused']+=1;continue
        source=source_by_id.get(cue['id'])
        if source and abs(source['start']-cue['start'])<.001 and abs(source['end']-cue['end'])<.001:
            tokens=lambda s:re.findall(r'\w+',s.casefold())
            if tokens(source['text'])!=tokens(cue['text']):
                cue['words']=[];stats['plain']+=1;continue
            if valid_words(source):
                cue['words']=align_existing([cue],source['words'])[0]['words']
                if valid_words(cue):stats['reused']+=1;continue
        pending.append(i)
    if not pending:return stats,[i for i in selected if not valid_words(cues[i])]
    audio=store.project_dir(project['id'])/'audio.wav'
    if not audio.is_file():
        # Captions by sentence remain usable when the optional alignment WAV
        # is unavailable; never block export just to add karaoke decoration.
        stats['plain']+=len(pending)
        return stats,[i for i in selected if not valid_words(cues[i])]
    signature=[audio.stat().st_size,audio.stat().st_mtime_ns]
    cache=audio.parent/'word-alignment'/'selected-v1';cache.mkdir(parents=True,exist_ok=True)
    paths={i:cache/(digest({'version':1,'source':signature,'asr':settings['asr_model'],
                           'start':cues[i]['start'],'end':cues[i]['end'],'text':cues[i]['text']})+'.json') for i in pending}
    remaining=[]
    for i in pending:
        try:
            saved=json.loads(paths[i].read_text('utf-8'))
            candidate={**cues[i],'words':saved['words']}
            if saved.get('complete') is True and (not saved['words'] or valid_words(candidate)):
                cues[i]['words']=saved['words'];stats['reused']+=1;continue
        except (OSError,ValueError,KeyError,TypeError):pass
        remaining.append(i)
    if not remaining:return stats,[i for i in selected if not valid_words(cues[i])]
    def save(i):
        if not valid_words(cues[i]):cues[i]['words']=[]
        path=paths[i];tmp=path.with_suffix('.tmp')
        tmp.write_text(json.dumps({'complete':True,'words':cues[i].get('words',[])},ensure_ascii=False),'utf-8')
        tmp.replace(path)
    # Reuse old whole-source recognition if it exists, never repeat that ASR.
    old=audio.parent/'word-alignment'/(digest({'audio':signature,'asr':settings['asr_model'],'version':1})+'.json')
    observed=None
    try:
        recognized=json.loads(old.read_text('utf-8'))
        observed=sorted([w for c in recognized for w in c.get('words',[])],key=lambda w:w['start'])
    except (OSError,ValueError,TypeError,KeyError):pass
    if observed is not None:
        for i in remaining:
            check();cues[i]['words']=align_existing([cues[i]],observed)[0]['words'];save(i)
        stats['reused']+=len(remaining)
        return stats,[i for i in selected if not valid_words(cues[i])]
    # Include whole boundary cues plus .25s context for alignment. Merge
    # nearby cues, then bound every Whisper input to 30s, avoiding a full
    # source pass just because one displayed sentence lacks word timings.
    duration=project['metadata']['duration']
    ranges=merge_windows([(max(0,cues[i]['start']-.25),min(duration,cues[i]['end']+.25)) for i in remaining],gap=.5)
    chunks=[]
    for a,b in ranges:
        while b-a>30:
            chunks.append((a,a+30));a+=29.5
        if b>a:chunks.append((a,b))
    total=sum(b-a for a,b in chunks);processed=0;observed=[];done=set()
    for index,(a,b) in enumerate(chunks):
        check()
        report(40+50*processed/max(1,total),f'Canh thoại gốc được dùng · đoạn {index+1}/{len(chunks)} · {processed:.0f}/{total:.0f}s…')
        wav=cache/'working.wav'
        try:
            run([FFMPEG,'-y','-ss',f'{a:.6f}','-i',audio,'-t',f'{b-a:.6f}',
                 '-ar','16000','-ac','1',wav],check_cancel=check)
            recognized=transcribe(wav,settings,check)
        finally:
            wav.unlink(missing_ok=True)
        for c in recognized:
            for w in c.get('words',[]):
                word={**w,'start':float(w['start'])+a,'end':float(w['end'])+a}
                # Overlapping context can recognize the same boundary word
                # twice. Keep its first anchor, never duplicate caption text.
                if any(old['text'].casefold()==word['text'].casefold()
                       and abs(old['start']-word['start'])<.2 and abs(old['end']-word['end'])<.2
                       for old in observed[-30:]):continue
                observed.append(word)
        processed+=b-a;stats['asr_calls']+=1;stats['transcribed_seconds']=round(processed,3)
        observed.sort(key=lambda w:w['start'])
        for i in remaining:
            if i in done or cues[i]['end']>b+.001:continue
            cues[i]['words']=align_existing([cues[i]],observed)[0]['words'];save(i);done.add(i)
        check()
    stats['plain']+=sum(not valid_words(cues[i]) for i in remaining)
    return stats,[i for i in selected if not valid_words(cues[i])]
