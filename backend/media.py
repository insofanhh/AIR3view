import json
import html
import math
import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
import av
from . import store

FFMPEG = os.environ.get('FFMPEG_PATH') or shutil.which('ffmpeg') or 'ffmpeg'
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
SOURCE_SUBTITLES_VERSION = 2


class Cancelled(Exception):
    pass


def run(args, cwd=None, check_cancel=lambda: None, timeout=7200):
    """Drain output to disk: no full pipe deadlocks during long renders."""
    import tempfile
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen([str(x) for x in args], cwd=cwd, stdout=log, stderr=log, creationflags=NO_WINDOW)
        started = time.monotonic()
        try:
            while process.poll() is None:
                check_cancel()
                if time.monotonic() - started > timeout:
                    raise TimeoutError('Quá thời gian xử lý. Có thể thử lại từ dữ liệu đã lưu.')
                time.sleep(0.2)
        except BaseException:
            process.kill()
            process.wait()
            raise
        log.seek(0)
        output = log.read().decode('utf-8', errors='replace')
        if process.returncode:
            raise RuntimeError(output[-5000:])
        return output


def probe(path):
    with av.open(str(path)) as container:
        video = next(iter(container.streams.video), None)
        audio = next(iter(container.streams.audio), None)
        if video:
            duration = float(video.duration * video.time_base) if video.duration is not None else float((container.duration or 0) / av.time_base)
        else:
            duration = float((container.duration or 0) / av.time_base)
        if duration <= 0:
            raise ValueError('Không đọc được thời lượng của file.')
        return {'duration': duration, 'width': video.width if video else 0, 'height': video.height if video else 0, 'fps': float(video.average_rate or 30) if video else 0, 'has_audio': audio is not None}


def prepare(project, report, check):
    folder = store.project_dir(project['id'])
    source = project['source']
    if source['kind'] == 'youtube' and not source.get('file'):
        from yt_dlp import YoutubeDL
        report(3, 'Đang tải video từ YouTube…')
        def progress(event):
            check()
            total = event.get('total_bytes') or event.get('total_bytes_estimate') or 0
            if total:
                report(min(25, 3 + 22 * event.get('downloaded_bytes', 0) / total), 'Đang tải video…')
        options = {'outtmpl': str(folder / 'source.%(ext)s'), 'format': 'bv*[height<=1080]+ba/b[height<=1080]/b', 'merge_output_format': 'mp4', 'noplaylist': True, 'quiet': True, 'ffmpeg_location': FFMPEG, 'progress_hooks': [progress], 'socket_timeout': 30, 'retries': 3}
        with YoutubeDL(options) as downloader:
            info = downloader.extract_info(source['url'], download=True)
            candidates = [p for p in folder.glob('source.*') if p.suffix.lower() in ('.mp4', '.mkv', '.webm', '.mov')]
            if not candidates:
                raise RuntimeError('Không tìm thấy file video đã tải.')
            source['file'] = max(candidates, key=lambda p: p.stat().st_size).name
            project['name'] = str(info.get('title') or project['name'])[:180]
        store.save(project)
    path = store.asset(project['id'], source['file'])
    metadata = probe(path)
    if not metadata['width']:
        raise ValueError('File không có track video.')
    project['metadata'] = metadata
    store.save(project)
    if not (folder / 'proxy.mp4').exists():
        report(28, 'Tạo video xem trước…')
        run([FFMPEG, '-y', '-i', path, '-map', '0:v:0', '-map', '0:a:0?', '-vf', "scale=w='min(960,iw)':h=-2", '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '25', '-c:a', 'aac', '-movflags', '+faststart', folder / 'proxy.tmp.mp4'], check_cancel=check)
        (folder / 'proxy.tmp.mp4').replace(folder / 'proxy.mp4')
    if metadata['has_audio'] and not (folder / 'audio.wav').exists():
        report(40, 'Tách âm thanh nguồn…')
        run([FFMPEG, '-y', '-i', path, '-vn', '-ac', '1', '-ar', '16000', folder / 'audio.wav'], check_cancel=check)
    if not project['frames']:
        report(48, 'Tìm chuyển cảnh và lấy khung hình…')
        import cv2
        from scenedetect import detect, AdaptiveDetector
        scenes = detect(str(folder / 'proxy.mp4'), AdaptiveDetector(min_scene_len=30), show_progress=False)
        check()
        ranges = [(a.get_seconds(), b.get_seconds()) for a, b in scenes] or [(0, metadata['duration'])]
        # Long continuous shots also need temporal coverage, not just a thumbnail.
        times = {0.1}
        for start, end in ranges:
            t = start + min(0.2, (end - start) / 2)
            while t < end:
                times.add(round(t, 3))
                t += 5
        cap = cv2.VideoCapture(str(folder / 'proxy.mp4'))
        (folder / 'frames').mkdir(exist_ok=True)
        frames = []
        for i, t in enumerate(sorted(times)):
            check()
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if ok:
                height, width = frame.shape[:2]
                frame = cv2.resize(frame, (int(width * min(1, 640 / width)), int(height * min(1, 640 / width))))
                relative = f'frames/{i:05d}.jpg'
                # imencode.tofile handles Vietnamese/Unicode paths on Windows.
                cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 83])[1].tofile(str(folder / relative))
                frames.append({'time': t, 'file': relative})
        cap.release()
        project['frames'] = frames
        project['shots'] = [{'start': a, 'end': b} for a, b in ranges]
    if source['kind'] == 'youtube' and not project['transcript']:
        try_source_subtitles(project, report, check)
    report(95, 'Nguồn đã sẵn sàng')
    return store.save(project)


_asr_cache = {}


def transcribe(path, settings, check=lambda: None, expected_text=None):
    """Recover from unavailable CUDA at model load or lazy audio decoding.

    Keep the same model and restart the whole audio on CPU so a partially
    yielded GPU transcript can never be duplicated or mistaken for completion.
    """
    try:
        return _transcribe(path, settings, check, expected_text)
    except (RuntimeError, OSError) as exc:
        message = str(exc).lower()
        cuda = any(name in message for name in ('cuda', 'cublas', 'cudnn', 'cudart'))
        unavailable = any(reason in message for reason in (
            'not found', 'cannot be loaded', 'could not load', 'failed to load',
            'driver version is insufficient', 'no cuda-capable device',
            'out of memory', 'no kernel image'))
        if settings['asr_device'] != 'cuda' or not (cuda and unavailable):
            raise
    # Leave the exception handler before allocating CPU memory, releasing its
    # traceback and the failed GPU inference references first.
    import gc
    import logging
    _asr_cache.clear()
    gc.collect()
    check()
    logging.getLogger(__name__).warning('CUDA ASR không khả dụng; nhận dạng và canh phụ đề lại bằng CPU int8.')
    result = _transcribe(path, {**settings, 'asr_device': 'cpu'}, check, expected_text)
    # Persist through the caller's normal project save; subsequent clips and
    # future jobs do not keep retrying the unavailable GPU. TTS is unaffected.
    settings['asr_device'] = 'cpu'
    return result


def _transcribe(path, settings, check=lambda: None, expected_text=None):
    from faster_whisper import WhisperModel
    key = (settings['asr_model'], settings['asr_device'])
    if key not in _asr_cache:
        _asr_cache.clear()
        _asr_cache[key] = WhisperModel(key[0], device=key[1], compute_type='int8' if key[1] == 'cpu' else 'float16', download_root=str(store.DATA / 'models'))
    language = {'Vietnamese': 'vi', 'English': 'en', 'Chinese': 'zh'}.get(settings['language']) if expected_text else None
    # Supplying the complete script as Whisper's prior context can make it
    # recognize only the final sentence of a long narration. Decode the audio
    # independently, then align the known text to those real timing anchors.
    segments, info = _asr_cache[key].transcribe(str(path), word_timestamps=True, vad_filter=True, beam_size=5, language=language)
    result = []
    timed_words = []
    for segment in segments:
        check()
        words = list(segment.words or [])
        timed_words.extend({'text': w.word, 'start': w.start, 'end': w.end} for w in words)
        # Group real word timings into readable short cues.
        if words:
            groups, group = [], []
            for word in words:
                # A pause must not leave a caption on screen through a silent scene.
                if group and (len(group) >= 8 or word.start - group[-1].end > .65 or word.end - group[0].start > 4.5):
                    groups.append(group)
                    group = []
                group.append(word)
            if group:
                groups.append(group)
            for group in groups:
                if group[-1].end > group[0].start:
                    result.append({'id': f'c{len(result)}', 'start': group[0].start, 'end': group[-1].end, 'text': ''.join(w.word for w in group).strip(), 'speaker': 'original', 'words': [{'text': w.word.strip(), 'start':max(0,w.start), 'end':max(0,w.end)} for w in group]})
        elif segment.end > segment.start:
            result.append({'id': f'c{len(result)}', 'start': segment.start, 'end': segment.end, 'text': segment.text.strip(), 'speaker': 'original'})
    if expected_text:
        from .alignment import align_script
        return align_script(expected_text, timed_words, probe(path)['duration'])
    return result


def parse_srt(text):
    text = text.replace('\r\n', '\n').lstrip('\ufeff')
    cues = []
    pattern = r'(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})'
    for block in re.split(r'\n\s*\n', text.strip()):
        lines = block.splitlines()
        for i, line in enumerate(lines):
            if '-->' not in line:
                continue
            matches = re.findall(pattern, line)
            if len(matches) != 2:
                continue
            seconds = [int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000 for h, m, s, ms in matches]
            if seconds[1] > seconds[0]:
                cues.append({'id': f'c{len(cues)}', 'start': seconds[0], 'end': seconds[1], 'text': '\n'.join(lines[i+1:]).strip(), 'speaker': 'original'})
            break
    if not cues:
        raise ValueError('Không đọc được mốc phụ đề. Hãy chọn file SRT UTF-8.')
    return cues


def _language_name(code):
    root = re.split(r'[-_]', str(code or '').lower())[0]
    return {'en': 'English', 'vi': 'Vietnamese', 'zh': 'Chinese'}.get(root, str(code or ''))


def _clean_caption_text(text):
    text = html.unescape(re.sub(r'<[^>]+>', '', str(text or '')))
    return re.sub(r'\s+', ' ', text).strip()


def _meaningful_caption(cue):
    text = cue.get('text', '').strip().casefold()
    text = re.sub(r'^[\[\(]|[\]\)]$', '', text).strip()
    if re.match(r'^(?:music|background music|applause|laughter|laughs|sound effect)\b', text):
        return False
    return text not in {'music', 'background music', 'applause', 'laughter', 'laughs',
                        'sound effect', 'speaking foreign language', '♪'}


def _timestamp(value):
    value = value.strip().replace(',', '.')
    parts = value.split(':')
    if len(parts) == 2:
        hours, minutes, seconds = 0, *parts
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        raise ValueError('Mốc phụ đề không hợp lệ.')
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _parse_vtt(text):
    cues = []
    text = text.replace('\r\n', '\n').lstrip('\ufeff')
    for block in re.split(r'\n\s*\n', text.strip()):
        lines = block.splitlines()
        for index, line in enumerate(lines):
            match = re.match(r'\s*([^\s]+)\s+-->\s+([^\s]+)', line)
            if not match:
                continue
            start, end = _timestamp(match.group(1)), _timestamp(match.group(2))
            caption = _clean_caption_text(' '.join(lines[index + 1:]))
            if caption and end > start:
                cues.append({'id': f'c{len(cues)}', 'start': start, 'end': end,
                             'text': caption, 'speaker': 'original'})
            break
    if not cues:
        raise ValueError('Không đọc được phụ đề WebVTT.')
    return cues


def _parse_json3(text):
    body = json.loads(text)
    cues = []
    for event in body.get('events', []):
        if not event.get('segs'):
            continue
        start = float(event.get('tStartMs', 0)) / 1000
        duration = float(event.get('dDurationMs', 0)) / 1000
        caption = _clean_caption_text(''.join(segment.get('utf8', '') for segment in event['segs']))
        if caption and duration > 0:
            cues.append({'id': f'c{len(cues)}', 'start': start, 'end': start + duration,
                         'text': caption, 'speaker': 'original'})
    if not cues:
        raise ValueError('Không đọc được phụ đề JSON3.')
    return cues


def _xml_time(value):
    value = str(value or '').strip()
    if value.endswith('ms'):
        return float(value[:-2]) / 1000
    if value.endswith('s'):
        return float(value[:-1])
    return _timestamp(value)


def _parse_ttml(text):
    root = ET.fromstring(text)
    cues = []
    for node in root.iter():
        if node.tag.rsplit('}', 1)[-1] != 'p' or 'begin' not in node.attrib:
            continue
        start = _xml_time(node.attrib['begin'])
        end = (_xml_time(node.attrib['end']) if node.attrib.get('end') else
               start + _xml_time(node.attrib.get('dur', '0s')))
        caption = _clean_caption_text(''.join(node.itertext()))
        if caption and end > start:
            cues.append({'id': f'c{len(cues)}', 'start': start, 'end': end,
                         'text': caption, 'speaker': 'original'})
    if not cues:
        raise ValueError('Không đọc được phụ đề TTML.')
    return cues


def _parse_source_subtitles(path):
    text = path.read_text('utf-8-sig')
    extension = path.suffix.lower()
    if extension == '.srt':
        return parse_srt(text)
    if extension == '.vtt':
        return _parse_vtt(text)
    if extension == '.json3':
        return _parse_json3(text)
    if extension in ('.ttml', '.xml'):
        return _parse_ttml(text)
    raise ValueError('Định dạng phụ đề YouTube không được hỗ trợ.')


def _normalized_source_cues(cues, duration):
    """Validate timing and collapse rolling automatic-caption revisions."""
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Thời lượng video không hợp lệ.')
    clean = []
    invalid = 0
    for cue in sorted(cues, key=lambda item: (item.get('start', 0), item.get('end', 0))):
        try:
            start, end = float(cue['start']), float(cue['end'])
        except (KeyError, TypeError, ValueError):
            invalid += 1
            continue
        text = _clean_caption_text(cue.get('text'))
        if (not text or not math.isfinite(start) or not math.isfinite(end) or
                start < 0 or end <= start or start >= duration + 1 or end > duration + .25):
            invalid += 1
            continue
        end = min(end, duration)
        if end <= start:
            invalid += 1
            continue
        candidate = {'id': '', 'start': start, 'end': end, 'text': text, 'speaker': 'original'}
        if clean:
            previous = clean[-1]
            same_window = abs(previous['start'] - start) <= .35 and start < previous['end']
            # YouTube automatic captions often emit growing revisions for the
            # same window. Keep only the most complete revision.
            if same_window and (text.startswith(previous['text']) or previous['text'].startswith(text)):
                if len(text) >= len(previous['text']):
                    candidate['start'] = min(start, previous['start'])
                    candidate['end'] = max(end, previous['end'])
                    clean[-1] = candidate
                continue
            if text.casefold() == previous['text'].casefold() and start <= previous['end'] + .2:
                previous['end'] = max(previous['end'], end)
                continue
        clean.append(candidate)
    if invalid > max(2, len(cues) // 10):
        raise ValueError('Phụ đề có quá nhiều mốc thời gian lỗi.')
    meaningful = [cue for cue in clean if _meaningful_caption(cue) and re.search(r'\w', cue['text'], re.UNICODE)]
    visible = sum(cue['end'] - cue['start'] for cue in meaningful)
    # This excludes empty/music-only tracks without penalizing a quiet video
    # that contains only one short, legitimate line of dialogue.
    if not meaningful or sum(len(re.findall(r'\w', cue['text'], re.UNICODE)) for cue in meaningful) < 2 \
            or visible < min(1.0, max(.15, duration * .001)):
        raise ValueError('Phụ đề quá ít nội dung có nghĩa.')
    for index, cue in enumerate(clean):
        cue['id'] = f'c{index}'
    return clean


def _subtitle_track(info):
    """Return one exact language/format, preferring human captions."""
    preferred = [str(info.get(key) or '') for key in ('original_language', 'language', 'audio_language')]
    preferred = [item.lower() for item in preferred if item]

    def language_score(language):
        value = language.lower()
        root = re.split(r'[-_]', value)[0]
        for position, wanted in enumerate(preferred):
            wanted_root = re.split(r'[-_]', wanted)[0]
            if value == wanted:
                return (0, position, value)
            if root == wanted_root:
                return (1, position, value)
        if value.endswith('-orig'):
            return (2, 0, value)
        supported = {'en': 0, 'vi': 1, 'zh': 2}
        return (3, supported.get(root, 99), value)

    format_rank = {'srt': 0, 'vtt': 1, 'json3': 2, 'ttml': 3}
    for field, origin in (('subtitles', 'youtube_subtitles'),
                          ('automatic_captions', 'youtube_auto_subtitles')):
        tracks = info.get(field) or {}
        for language in sorted(tracks, key=language_score):
            formats = {str(item.get('ext', '')).lower() for item in (tracks.get(language) or [])}
            compatible = sorted(formats & format_rank.keys(), key=format_rank.get)
            if compatible:
                return origin, language, compatible[0]
    return None


def _caption_warning(project, message):
    warnings = project.setdefault('warnings', [])
    if message not in warnings:
        warnings.append(message)


def try_source_subtitles(project, report=lambda *_: None, check=lambda: None):
    """Install one validated YouTube caption track without invoking ASR.

    The project is mutated but not saved. Existing transcripts are never
    replaced. A versioned URL marker avoids repeating failed network work while
    still allowing old `source_subtitles_checked` projects one improved retry.
    """
    if project.get('transcript'):
        return True
    # A localized/edited transcript may have been cleared while its original
    # copy remains. Preserve that source rather than fetching and replacing it.
    if project.get('source_transcript'):
        project['transcript'] = [dict(cue) for cue in project['source_transcript']]
        return True
    source = project.get('source') or {}
    if source.get('kind') != 'youtube' or not source.get('url'):
        return False
    marker = {'version': SOURCE_SUBTITLES_VERSION, 'url': source['url']}
    if project.get('source_subtitles_check') == marker:
        return False

    from yt_dlp import YoutubeDL
    folder = store.project_dir(project['id'])
    project['source_subtitles_check'] = marker
    project['source_subtitles_checked'] = True
    report(93, 'Tìm phụ đề gốc trên YouTube…')
    try:
        check()
        inspect_options = {'skip_download': True, 'noplaylist': True, 'quiet': True,
                           'socket_timeout': 15, 'retries': 1}
        with YoutubeDL(inspect_options) as downloader:
            info = downloader.extract_info(source['url'], download=False) or {}
        check()
        selected = _subtitle_track(info)
        if not selected:
            _caption_warning(project, 'YouTube không có phụ đề phù hợp; sẽ nhận dạng từ âm thanh nguồn.')
            return False
        origin, language, extension = selected

        # Remove only files created by either this or the previous caption
        # downloader. A fresh prefix prevents an old language/format from
        # being mistaken for the selected track.
        stale_files = list(folder.glob('source-captions.*')) + list(folder.glob('captions.*'))
        for stale in stale_files:
            if stale.is_file():
                stale.unlink()
        options = {'skip_download': True, 'writesubtitles': origin == 'youtube_subtitles',
                   'writeautomaticsub': origin == 'youtube_auto_subtitles',
                   'subtitleslangs': [language], 'subtitlesformat': extension,
                   'outtmpl': str(folder / 'source-captions.%(ext)s'), 'noplaylist': True,
                   'quiet': True, 'socket_timeout': 15, 'retries': 1,
                   'ffmpeg_location': FFMPEG}
        with YoutubeDL(options) as downloader:
            downloader.extract_info(source['url'], download=True)
        check()
        files = [path for path in folder.glob('source-captions.*')
                 if path.is_file() and path.suffix.lower() in ('.srt', '.vtt', '.json3', '.ttml', '.xml')]
        if len(files) != 1:
            raise ValueError('YouTube không tạo đúng một file phụ đề đã chọn.')
        cues = _normalized_source_cues(_parse_source_subtitles(files[0]),
                                       float(project.get('metadata', {}).get('duration') or info.get('duration') or 0))
        project['transcript'] = cues
        project['source_transcript'] = [dict(cue) for cue in cues]
        project['transcript_origin'] = origin
        project['transcript_language'] = _language_name(language)
        project['source_subtitles'] = files[0].name
        return True
    except Cancelled:
        project.pop('source_subtitles_check', None)
        raise
    except Exception:
        _caption_warning(project, 'Không lấy được phụ đề YouTube hợp lệ; sẽ nhận dạng từ âm thanh nguồn.')
        return False
