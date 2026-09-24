"""Scene-boundary video cache, independent PCM mix, and one final AAC mux."""
import copy
import json
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import av
from . import store
from .providers import digest
from .timeline import build, slice_clips
from .media import FFMPEG, probe, NO_WINDOW, Cancelled
from .render_progress import run_progress

VERSION = 1
CHUNK_SECONDS = 45
VISUAL_SETTINGS = ('background','background_mode','fit','crop_x','crop_y','layout_preset',
                   'source_subtitle_blur','source_subtitle_blur_height')


def ticks(seconds):return round(seconds*30)


def file_identity(path):
    path=Path(path)
    stat=path.stat()
    return dict(path=str(path.resolve()),size=stat.st_size,mtime=stat.st_mtime_ns)


def save_json(path,value):
    temporary=path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),'utf-8')
    temporary.replace(path)


def load_json(path):
    try:
        value=json.loads(path.read_text('utf-8'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}


def chunks(timeline,part):
    """Never restart fps conversion inside a scene just to hit a chunk quota."""
    a,b=ticks(part['start']),ticks(part['end'])
    boundaries=sorted({ticks(c['end']) for c in timeline['clips'] if a<ticks(c['end'])<b}|{b})
    rows=[]
    for end in boundaries:
        if end-a>=CHUNK_SECONDS*30 or end==b:
            rows.append(dict(index=part['index'],start=a/30,end=end/30,duration=(end-a)/30))
            a=end
    return rows


def clip_key(timeline,part):
    return [dict(kind=c['kind'],source_start=round(c['source_start'],6),source_end=round(c['source_end'],6),
                 duration=ticks(c['end']-c['start'])) for c in slice_clips(timeline,part['start'],part['end'])]


def ranges(timeline,name,part):
    if name not in timeline:return None
    return [dict(start=round(max(0,c['start']-part['start']),6),
                 end=round(min(part['duration'],c['end']-part['start']),6))
            for c in timeline[name] if c['start']<part['end'] and c['end']>part['start']]


def video_key(project,timeline,part,width,encoder):
    from .render import subtitle_documents, video_encoder_args
    import os
    ass,_=subtitle_documents(project,timeline,part)
    font=Path(os.environ.get('AIR3VIEW_FONT','C:/Windows/Fonts/arialbd.ttf' if os.name=='nt' else '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
    return digest(dict(version=VERSION,source=file_identity(store.asset(project['id'],project['source']['file'])),
        clips=clip_key(timeline,part),width=width,encoder=video_encoder_args(encoder),
        visual={k:project['settings'].get(k) for k in VISUAL_SETTINGS},ass=ass,
        font=file_identity(font) if font.is_file() else str(font)))[:32]


def audio_key(project,timeline,part):
    settings=project['settings']
    voices=[dict(start=round(v['start']-part['start'],6),end=round(v['end']-part['start'],6),
                 file=file_identity(store.asset(project['id'],v['audio']))) for v in timeline['voices']
            if v['start']<part['end'] and v['end']>part['start']]
    return digest(dict(version=VERSION,source=file_identity(store.asset(project['id'],project['source']['file'])),
        clips=clip_key(timeline,part),samples=round(part['duration']*48000),has_audio=project['metadata']['has_audio'],
        voices=voices,volume={k:settings[k] for k in ('original_volume','duck_volume','voice_volume')},
        original=ranges(timeline,'original_audio',part),muted=ranges(timeline,'source_mutes',part)))[:32]


def valid_cache(folder,name,kind,duration,width=0):
    meta=load_json(folder/'ready.json')
    path=folder/name
    try:
        if meta.get('file')!=file_identity(path):return False
        info=probe(path)
        return (abs(info['duration']-duration)<.05 and
                (info['width']==width and info['height']==round(width*16/9) if kind=='video' else info['has_audio']))
    except (OSError,ValueError,av.error.FFmpegError):return False


def signature(path):
    with av.open(str(path)) as container:
        v=container.streams.video[0]
        return (v.codec_context.name,v.width,v.height,str(v.average_rate),
                str(v.time_base),digest((v.codec_context.extradata or b'').hex()))


def mix_audio(project,timeline,part,folder,check,progress):
    """Mix continuously to PCM; encode AAC only once after all video chunks."""
    s=project['settings'];filters=[];streams=[];args=[FFMPEG,'-y'];inputs=0
    source=store.asset(project['id'],project['source']['file'])
    clips=slice_clips(timeline,part['start'],part['end'])
    if project['metadata']['has_audio'] and s['original_volume']>0:
        for i,c in enumerate(clips):
            length=c['end']-c['start']
            if c['kind']=='freeze':
                filters.append(f'anullsrc=r=48000:cl=stereo,atrim=duration={length:.6f}[a{i}]')
            else:
                point=min(c['source_start'],max(0,project['metadata']['duration']-.1));seek=max(0,point-.1)
                args+=['-threads','1','-ss',f'{seek:.6f}','-t',f'{length+point-seek+.2:.6f}','-i',str(source)]
                filters.append(f'[{inputs}:a]atrim=start={point-seek:.6f}:duration={length:.6f},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,apad,atrim=duration={length:.6f}[a{i}]')
                inputs+=1
            streams.append(f'[a{i}]')
        filters.append(''.join(streams)+f'concat=n={len(clips)}:v=0:a=1[original]')
    else:
        filters.append(f'anullsrc=r=48000:cl=stereo,atrim=duration={part["duration"]:.6f}[original]')
    voices=[v for v in timeline['voices'] if v['end']>part['start'] and v['start']<part['end']]
    def gate(rows):
        return '+'.join(f'gte(t,{r["start"]:.6f})*lt(t,{r["end"]:.6f})' for r in rows) or '0'
    voiced=[dict(start=max(0,v['start']-part['start']),end=min(part['duration'],v['end']-part['start'])) for v in voices]
    level=f"if(gt({gate(voiced)},0),{s['duck_volume']},1)"
    original=ranges(timeline,'original_audio',part)
    if original is not None:level=f"if(gt({gate(original)},0),{level},{s['duck_volume']})"
    muted=ranges(timeline,'source_mutes',part) or []
    if muted:level=f'if(gt({gate(muted)},0),0,{level})'
    filters.append(f"[original]volume='{s['original_volume']}*({level})':eval=frame[ducked]")
    mixed=['[ducked]']
    for i,v in enumerate(voices):
        args+=['-i',str(store.asset(project['id'],v['audio']))]
        left=max(0,part['start']-v['start']);right=min(v['end']-v['start'],part['end']-v['start'])
        delay=max(0,round((v['start']-part['start'])*48000))
        filters.append(f'[{inputs+i}:a]atrim=start={left:.6f}:end={right:.6f},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,volume={s["voice_volume"]},adelay={delay}S:all=1[voice{i}]')
        mixed.append(f'[voice{i}]')
    filters.append(''.join(mixed)+f'amix=inputs={len(mixed)}:duration=first:normalize=0,alimiter=limit=0.95:latency=1,apad,atrim=end_sample={round(part["duration"]*48000)}[outa]')
    graph=folder/'audio.filters.txt';graph.write_text(';\n'.join(filters),'utf-8')
    temporary=folder/'mix.tmp.wav'
    args+=['-filter_complex_threads','2','-filter_complex_script',str(graph),'-map','[outa]','-vn','-c:a','pcm_s16le','-ar','48000','-ac','2',str(temporary)]
    run_progress(args,folder,check,part['duration'],progress)
    if abs(probe(temporary)['duration']-part['duration'])>.001:raise ValueError('Audio cache chưa đúng số mẫu theo lịch dựng.')
    check();temporary.replace(folder/'mix.wav')
    save_json(folder/'ready.json',dict(file=file_identity(folder/'mix.wav')))


def preview_part(timeline,start):
    start=max(0,min(ticks(start),ticks(timeline['duration'])-1))/30
    part=next(p for p in timeline['parts'] if p['start']<=start<p['end'])
    end=min(part['end'],start+15)
    return dict(index=part['index'],start=start,end=end,duration=end-start)


def render_workers(width,encoder,count):
    """At most two encode sessions, and only with measured GPU headroom."""
    if width<1080 or count<2 or encoder!='nvenc' or (os.cpu_count() or 1)<8:return 1
    try:
        result=subprocess.run(['nvidia-smi','--query-gpu=memory.free','--format=csv,noheader,nounits'],
                              capture_output=True,text=True,timeout=3,creationflags=NO_WINDOW)
        free=int(result.stdout.splitlines()[0]) if result.returncode==0 else 0
        return 2 if free>=768 else 1
    except (OSError,ValueError,IndexError,subprocess.SubprocessError):return 1


def render_cached(project,report,check,preview=False,preview_start=0):
    from . import render as renderer
    timeline=build(project,strict=True)
    if not timeline['clips']:raise ValueError('Chưa có video để xuất.')
    parts=[preview_part(timeline,preview_start)] if preview else timeline['parts']
    width=360 if preview else 1080
    root=store.project_dir(project['id'])
    cache=root/'render-cache';cache.mkdir(exist_ok=True)
    preference=project['settings'].get('render_encoder','auto')
    encoder='nvenc' if preference=='nvenc' or (preference=='auto' and renderer.nvenc_available()) else 'cpu'
    total=sum(p['duration'] for p in parts);completed=0;exports=[];last_progress=0
    def stage(part,begin,span,label):
        def update(done,duration,speed):
            nonlocal last_progress
            percent=1+98*(completed+part['duration']*(begin+span*min(1,done/max(.001,duration))))/total
            last_progress=max(last_progress,min(99,percent))
            elapsed=f'{int(done)//60:02}:{int(done)%60:02}/{int(duration)//60:02}:{int(duration)%60:02}'
            eta=f' · {speed:.1f}× · còn khoảng {max(0,round((duration-done)/speed))}s' if speed else ''
            report(last_progress,f'{label} · phần {part["index"]} · {elapsed}{eta}')
        return update
    for part in parts:
        check();rows=chunks(timeline,part)
        workers=render_workers(width,encoder,len(rows))
        reused=0;rendered=0
        while True:
            video_files=[];video_keys=[]
            aborted=threading.Event();progress_lock=threading.Lock();key_locks={}
            done={i:0.0 for i in range(len(rows))};speeds={i:0.0 for i in range(len(rows))}
            specs=[]
            for index,chunk in enumerate(rows):
                key=video_key(project,timeline,chunk,width,encoder)
                key_locks.setdefault(key,threading.Lock())
                specs.append((index,chunk,key))
            def local_check():
                if aborted.is_set():raise Cancelled('Dừng nhóm dựng để giữ cache đã hoàn tất.')
                check()
            def encode_chunk(spec):
                index,chunk,key=spec
                def progress(value,duration,speed):
                    with progress_lock:
                        done[index]=min(value,chunk['duration']);speeds[index]=speed if value<duration else 0
                        label='Dùng lại hình' if hit else f'Dựng hình · {workers} luồng'
                        stage(part,0,.78,f'{label} · đoạn {index+1}/{len(rows)}')(
                            sum(done.values()),part['duration'],sum(speeds.values()))
                with key_locks[key]:
                    local_check()
                    folder=cache/'video'/key;folder.mkdir(parents=True,exist_ok=True)
                    name=f'part-{part["index"]:03d}.mp4'
                    hit=valid_cache(folder,name,'video',chunk['duration'],width)
                    if not hit:
                        progress(0,chunk['duration'],0)
                        candidate=copy.deepcopy(project);candidate['settings']['render_encoder']=encoder
                        renderer.render_part(candidate,timeline,chunk,folder,local_check,width,video_only=True,progress=progress)
                        local_check();save_json(folder/'ready.json',dict(file=file_identity(folder/name),encoder=encoder))
                    progress(chunk['duration'],chunk['duration'],0)
                    return index,folder/name,key,hit
            try:
                completed_chunks=[]
                if workers==1:
                    completed_chunks=[encode_chunk(spec) for spec in specs]
                else:
                    with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='render-chunk') as executor:
                        futures=[executor.submit(encode_chunk,spec) for spec in specs]
                        try:
                            for future in as_completed(futures):completed_chunks.append(future.result())
                        except BaseException:
                            aborted.set()
                            for future in futures:future.cancel()
                            raise
                for _,path,key,hit in sorted(completed_chunks):
                    video_files.append(path);video_keys.append(key)
                    reused+=int(hit);rendered+=int(not hit)
                break
            except RuntimeError as exc:
                if encoder!='nvenc' or not renderer.device_error(exc):raise
                if workers>1:
                    workers=1
                    report(last_progress,'GPU thiếu tài nguyên; giảm còn một luồng và dùng lại các đoạn đã xong.')
                    continue
                if preference!='auto':raise
                check();renderer._NVENC=False;encoder='cpu'
                report(last_progress,'GPU không sẵn sàng; chuyển cả phần sang CPU để giữ đồng nhất các đoạn.')
        key_audio=audio_key(project,timeline,part)
        afolder=cache/'audio'/key_audio;afolder.mkdir(parents=True,exist_ok=True)
        audio_reused=valid_cache(afolder,'mix.wav','audio',part['duration'])
        if not audio_reused:
            progress=stage(part,.78,.14,'Trộn âm thanh');progress(0,part['duration'],0)
            mix_audio(project,timeline,part,afolder,check,progress)
        else:stage(part,.92,0,'Dùng lại âm thanh')(0,part['duration'],0)
        ass,srt=renderer.subtitle_documents(project,timeline,part)
        output_key=digest(dict(version=VERSION,video=video_keys,audio=key_audio,ass=ass,srt=srt,width=width))[:24]
        folder=root/'renders'/output_key;folder.mkdir(parents=True,exist_ok=True)
        name=renderer.write_subtitles(folder,project,timeline,part)
        manifest=folder/(name+'.result.json')
        result=load_json(manifest)
        output=folder/(name+'.mp4')
        final_reused=False
        try:final_reused=result.get('identity')==file_identity(output) and abs(probe(output)['duration']-part['duration'])<.05
        except (OSError,ValueError,av.error.FFmpegError):pass
        if not final_reused:
            if len({signature(path) for path in video_files})!=1:
                raise ValueError('Các đoạn cache khác cấu hình codec; chưa ghép để tránh lỗi phát hình.')
            concat=folder/'video.ffconcat'
            def quoted(path):return "'"+path.resolve().as_posix().replace("'","'\\''")+"'"
            concat.write_text('ffconcat version 1.0\n'+''.join(f'file {quoted(path)}\nduration {chunk["duration"]:.9f}\n' for path,chunk in zip(video_files,rows)),'utf-8')
            temporary=folder/(name+'.tmp.mp4')
            args=[FFMPEG,'-y','-f','concat','-safe','0','-i',str(concat),'-i',str(afolder/'mix.wav'),
                  '-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','192k','-t',f'{part["duration"]:.9f}',
                  '-movflags','+faststart',str(temporary)]
            progress=stage(part,.92,.065,'Ghép MP4');progress(0,part['duration'],0)
            run_progress(args,folder,check,part['duration'],progress)
            report(last_progress,'Kiểm tra thời lượng, kích thước và tiếng của MP4…')
            info=probe(temporary)
            if abs(info['duration']-part['duration'])>.05 or info['width']!=width or info['height']!=round(width*16/9) or not info['has_audio']:
                raise ValueError('MP4 chưa đạt kiểm tra cuối; giữ cache để thử lại.')
            check();temporary.replace(output)
        info=probe(output)
        result=dict(part=part['index'],file=output.relative_to(root).as_posix(),srt=(folder/(name+'.srt')).relative_to(root).as_posix(),
                    ass=(folder/(name+'.ass')).relative_to(root).as_posix(),duration=info['duration'],width=width,height=info['height'],encoder=encoder,
                    identity=file_identity(output),preview=preview,preview_start=part['start'] if preview else None,
                    cache=dict(video_reused=reused,video_rendered=rendered,audio_reused=audio_reused,final_reused=final_reused))
        save_json(manifest,result)
        exports.append(result);project['preview_exports' if preview else 'exports']=exports
        check();store.save(project)
        save_json(folder/'timeline.json',timeline)
        completed+=part['duration']
    project['warnings']=list(dict.fromkeys(project['warnings']+timeline['warnings']))
    report(100,f'Đã xuất bản xem thử {total:.1f}s' if preview else 'Đã xuất video')
    return store.save(project)
