import json
import math
import re
import os
import textwrap
from pathlib import Path
from PIL import ImageFont
from . import store
from .media import FFMPEG, run, probe
from .timeline import build, slice_clips
from .providers import digest


def ass_time(seconds):
    cs = max(0, round(seconds * 100))
    return f'{cs//360000}:{cs//6000%60:02}:{cs//100%60:02}.{cs%100:02}'


def srt_time(seconds):
    ms = max(0, round(seconds * 1000))
    return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'


def safe_text(text):
    return text.replace('\\', '／').replace('{', '（').replace('}', '）').replace('\r', '').replace('\n', r'\N')


def wrap_caption(text, size, width=940, max_lines=2):
    font_path = os.environ.get('AIR3VIEW_FONT', 'C:/Windows/Fonts/arialbd.ttf' if os.name == 'nt' else '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')
    for actual in range(size, 19, -2):
        try:
            font = ImageFont.truetype(font_path, actual)
        except OSError:
            font = ImageFont.load_default(size=actual)
        lines = ['']
        for word in text.split():
            candidate = (lines[-1] + ' ' + word).strip()
            if font.getlength(candidate) > width and lines[-1]:
                lines.append(word)
            else:
                lines[-1] = candidate
        if len(lines) <= max_lines:
            return r'{\fs' + str(actual) + '}' + r'\N'.join(safe_text(line) for line in lines)
    # Preserve all text; editor can shorten unusually long user captions.
    return r'{\fs20}' + r'\N'.join(safe_text(line) for line in lines)


def ass_color(hex_color):
    return '&H00' + hex_color[5:7] + hex_color[3:5] + hex_color[1:3]


def caption_events(cue, settings, part):
    r"""Full-cue events with only the currently spoken word colored.

    Use absolute word timestamps, including gaps, rather than progressive \k
    tags (which would leave previous words highlighted and drift after splits).
    """
    start, end = max(cue['start'], part['start']), min(cue['end'], part['end'])
    if end <= start:
        return []
    wrapped = wrap_caption(cue['text'], settings['subtitle_size'])
    prefix, body = wrapped.split('}', 1)
    chunks = re.split(r'(\\N|\s+)', body)
    tokens = [i for i, token in enumerate(chunks) if token and token != r'\N' and not token.isspace()]
    words = cue.get('words', [])
    # Editing/translation may invalidate old timing data; never highlight the
    # wrong token. Cues without reliable words remain readable plain captions.
    valid = bool(words) and ' '.join(w['text'] for w in words).split() == cue['text'].split() and len(words) == len(tokens)
    active = settings.get('subtitle_highlight', True) and valid
    boundaries = {start, end}
    if active:
        boundaries.update(max(start,min(end,w[k])) for w in words for k in ('start','end'))
    points = sorted(boundaries)
    result = []
    for left, right in zip(points, points[1:]):
        if round(right*100) <= round(left*100):
            continue
        content = list(chunks)
        if active:
            middle = (left+right)/2
            selected = next((i for i,w in enumerate(words) if w['start'] <= middle < w['end']), None)
            if selected is not None:
                index = tokens[selected]
                highlight = ass_color(settings.get('subtitle_highlight_color', '#38bdf8'))
                base = ass_color(settings['subtitle_color'])
                content[index] = r'{\1c' + highlight + '}' + content[index] + r'{\1c' + base + '}'
        text = prefix + '}' + ''.join(content)
        result.append(f"Dialogue: 1,{ass_time(left-part['start'])},{ass_time(right-part['start'])},Sub,,0,0,0,,{text}")
    return result


def layout(settings):
    if settings.get('layout_preset') == 'reference':
        return dict(top=450, height=1000, inside=1340, below=1480, part=1580)
    return dict(top=330, height=1080, inside=1310, below=1470, part=1760)


def write_subtitles(folder, project, timeline, part):
    settings = project['settings']
    duration = part['duration']
    geometry = layout(settings)
    header = f'''[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,Arial,{settings['title_size']},&H00FFFFFF,&H00FFFFFF,&H90000000,&H90000000,-1,0,0,0,100,100,0,0,1,2,0,8,65,65,130,1
Style: Sub,Arial,{settings['subtitle_size']},{ass_color(settings['subtitle_color'])},&H00FFFFFF,&H00202020,&H90000000,-1,0,0,0,100,100,0,0,1,3,1,8,65,65,{geometry[settings['subtitle_position']]},1
Style: Part,Arial,32,&H00FFFFFF,&H00FFFFFF,&H70000000,&H70000000,-1,0,0,0,100,100,1,0,3,8,0,8,50,50,{geometry["part"]},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    part_label = {'English': 'PART', 'Chinese': '第'}.get(settings['language'], 'PHẦN')
    events = [f"Dialogue: 0,0:00:00.00,{ass_time(duration)},Title,,0,0,0,,{wrap_caption(settings['title'], settings['title_size'], max_lines=3)}", f"Dialogue: 0,0:00:00.00,{ass_time(duration)},Part,,0,0,0,,{part_label} {part['index']}"]
    if settings.get('output_mode') == 'single':
        events = [e for e in events if ',Part,' not in e]
    if settings.get('layout_preset') == 'reference':
        # A single dark title panel, matching the supplied finished-video references.
        box = r'{\an7\pos(120,240)\p1\bord0\shad0\1c&H000000&\1a&H20&}m 0 0 l 840 0 840 175 0 175'
        title = r'{\an5\pos(540,327)}' + wrap_caption(settings['title'], settings['title_size'], width=800, max_lines=3)
        events[:1] = [
            f"Dialogue: 0,0:00:00.00,{ass_time(duration)},Title,,0,0,0,,{box}",
            f"Dialogue: 1,0:00:00.00,{ass_time(duration)},Title,,0,0,0,,{title}",
        ]
    srt = []
    for cue in timeline['cues']:
        a, b = max(cue['start'], part['start']) - part['start'], min(cue['end'], part['end']) - part['start']
        if b <= a:
            continue
        srt.append(f"{len(srt)+1}\n{srt_time(a)} --> {srt_time(b)}\n{cue['text']}\n")
        if settings['subtitles']:
            events.extend(caption_events(cue, settings, part))
    name = f"part-{part['index']:03d}"
    (folder / (name + '.ass')).write_text(header + '\n'.join(events), 'utf-8-sig')
    (folder / (name + '.srt')).write_text('\n'.join(srt), 'utf-8-sig')
    return name


def render_part(project, timeline, part, folder, check, width=1080):
    settings = project['settings']
    geometry = layout(settings)
    height = geometry['height']
    source = store.asset(project['id'], project['source']['file'])
    name = write_subtitles(folder, project, timeline, part)
    clips = slice_clips(timeline, part['start'], part['end'])
    voices = [v for v in timeline['voices'] if v['end'] > part['start'] and v['start'] < part['end']]
    # Give each clip its own bounded, seeked input. Reusing one decoded input
    # for a later hook followed by an earlier source section made concat buffer
    # hundreds of full-HD frames and exhaust RAM before the hook finished.
    args = [FFMPEG, '-y']
    starts = []
    for c in clips:
        point = min(c['source_start'], max(0, project['metadata']['duration'] - .1))
        seek = max(0, point - .1)
        length = .1 if c['kind'] == 'freeze' else c['end'] - c['start']
        starts.append(point - seek)
        args += ['-threads', '1', '-ss', f'{seek:.6f}', '-t', f'{length+point-seek+.2:.6f}', '-i', str(source)]
    for voice in voices:
        args += ['-i', str(store.asset(project['id'], voice['audio']))]
    filters, streams = [], []
    for i, c in enumerate(clips):
        length = c['end'] - c['start']
        start = starts[i]
        if c['kind'] == 'freeze':
            filters.append(f'[{i}:v]trim=start={start:.6f}:duration=0.05,setpts=PTS-STARTPTS,fps=30,tpad=stop_mode=clone:stop_duration={length:.6f},trim=duration={length:.6f},setsar=1[v{i}]')
            filters.append(f'anullsrc=r=48000:cl=stereo,atrim=duration={length:.6f}[a{i}]')
        else:
            filters.append(f'[{i}:v]trim=start={start:.6f}:duration={length:.6f},setpts=PTS-STARTPTS,fps=30,tpad=stop_mode=clone:stop_duration=1,trim=duration={length:.6f},setsar=1[v{i}]')
            if project['metadata']['has_audio']:
                filters.append(f'[{i}:a]atrim=start={start:.6f}:duration={length:.6f},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,apad,atrim=duration={length:.6f}[a{i}]')
            else:
                filters.append(f'anullsrc=r=48000:cl=stereo,atrim=duration={length:.6f}[a{i}]')
        streams.append(f'[v{i}][a{i}]')
    filters.append(''.join(streams) + f'concat=n={len(clips)}:v=1:a=1[video][original]')
    color = settings['background']
    if settings['background_mode'] == 'blur':
        filters += ['[video]split=2[fgin][bgin]', '[bgin]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,gblur=sigma=15,scale=1080:1920[background]']
    else:
        filters += [f'color=c={color}:s=1080x1920:r=30:d={part["duration"]}[background]', '[video]null[fgin]']
    if settings['fit'] == 'cover':
        x, y = settings['crop_x'] / 100, settings['crop_y'] / 100
        filters.append(f'[fgin]scale=1080:{height}:force_original_aspect_ratio=increase,crop=1080:{height}:(iw-1080)*{x}:(ih-{height})*{y}[square]')
    else:
        filters.append(f'[fgin]scale=1080:{height}:force_original_aspect_ratio=decrease,pad=1080:{height}:(ow-iw)/2:(oh-ih)/2:color={color}[square]')
    if settings.get('source_subtitle_blur'):
        band = int(height * .22) // 2 * 2
        filters += [
            '[square]split=2[base][captionarea]',
            f'[captionarea]crop=1080:{band}:0:{height-band},scale=270:{band//4},gblur=sigma=8,scale=1080:{band}[softcaptionarea]',
            f'[base][softcaptionarea]overlay=0:{height-band}:shortest=1[clean_square]',
        ]
    else:
        filters.append('[square]null[clean_square]')
    filters.append(f'[background][clean_square]overlay=0:{geometry["top"]}:shortest=1,ass=filename={name}.ass,scale={width}:{round(width*16/9)},format=yuv420p,setsar=1[outv]')
    duck = '+'.join(f'between(t,{max(0,v["start"]-part["start"]):.6f},{min(part["duration"],v["end"]-part["start"]):.6f})' for v in voices) or '0'
    gate = '1'
    if 'original_audio' in timeline:
        gate = '+'.join(f'between(t,{max(0,x["start"]-part["start"]):.6f},{min(part["duration"],x["end"]-part["start"]):.6f})'
                        for x in timeline['original_audio'] if x['end']>part['start'] and x['start']<part['end']) or '0'
    # Narrated scenes keep a quiet background through pauses too. Only the
    # selected original-dialogue intervals return to full source volume.
    level = f"if(gt({duck},0),{settings['duck_volume']},1)"
    if 'original_audio' in timeline:
        level = f"if(gt({gate},0),{level},{settings['duck_volume']})"
    filters.append(f"[original]volume='{settings['original_volume']}*({level})':eval=frame[ducked]")
    mix = ['[ducked]']
    for i, v in enumerate(voices):
        a = max(0, part['start'] - v['start'])
        b = min(v['end'] - v['start'], part['end'] - v['start'])
        delay = max(0, round((v['start'] - part['start']) * 48000))
        filters.append(f'[{i+len(clips)}:a]atrim=start={a:.6f}:end={b:.6f},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,volume={settings["voice_volume"]},adelay={delay}S:all=1[voice{i}]')
        mix.append(f'[voice{i}]')
    filters.append(''.join(mix) + f'amix=inputs={len(mix)}:duration=first:normalize=0,alimiter=limit=0.95:latency=1[outa]')
    graph = folder / (name + '.filters.txt')
    graph.write_text(';\n'.join(filters), 'utf-8')
    output = folder / (name + '.mp4')
    temporary = folder / (name + '.tmp.mp4')
    args += ['-filter_complex_threads', '2', '-filter_complex_script', graph.name, '-map', '[outv]', '-map', '[outa]', '-t', f'{part["duration"]:.6f}', '-r', '30', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-threads', '4', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(temporary)]
    run(args, cwd=folder, check_cancel=check)
    metadata = probe(temporary)
    if abs(metadata['duration'] - part['duration']) > .1 or metadata['width'] != width:
        raise RuntimeError('Kiểm tra file xuất không đạt thời lượng/kích thước. Chưa công bố file này.')
    temporary.replace(output)
    return {'part': part['index'], 'file': output.relative_to(store.project_dir(project['id'])).as_posix(), 'srt': (folder / (name + '.srt')).relative_to(store.project_dir(project['id'])).as_posix(), 'ass': (folder / (name + '.ass')).relative_to(store.project_dir(project['id'])).as_posix(), 'duration': metadata['duration'], 'width': metadata['width'], 'height': metadata['height']}


def render(project, report, check, preview=False):
    timeline = build(project, strict=True)
    if not timeline['clips']:
        raise ValueError('Chưa có video để xuất.')
    fingerprint = digest({'source': project['source'], 'settings': project['settings'], 'narrations': project['narrations'], 'transcript': project['transcript'], 'preview': preview, 'renderer': 10, 'story_plan': project.get('story_plan')})[:16]
    folder = store.project_dir(project['id']) / 'renders' / fingerprint
    folder.mkdir(parents=True, exist_ok=True)
    parts = timeline['parts'][:1] if preview else timeline['parts']
    exports = []
    for i, part in enumerate(parts):
        check()
        report(5 + 90 * i / len(parts), f"Đang dựng {'bản xem thử' if preview else 'phần'} {part['index']}/{len(parts)}…")
        manifest = folder / f"part-{part['index']:03d}.result.json"
        result = None
        if manifest.exists():
            candidate = json.loads(manifest.read_text('utf-8'))
            if all(store.asset(project['id'], candidate[k]).is_file() for k in ('file', 'srt', 'ass')):
                result = candidate
        if result is None:
            result = render_part(project, timeline, part, folder, check, width=360 if preview else 1080)
            manifest.write_text(json.dumps(result), 'utf-8')
        exports.append(result)
        project['preview_exports' if preview else 'exports'] = exports
        store.save(project)
    (folder / 'timeline.json').write_text(json.dumps(timeline, ensure_ascii=False, indent=2), 'utf-8')
    project['warnings'] = list(dict.fromkeys(project['warnings'] + timeline['warnings']))
    report(100, 'Đã xuất video')
    return store.save(project)
