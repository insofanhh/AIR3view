import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
import av
from . import store

FFMPEG = os.environ.get('FFMPEG_PATH') or shutil.which('ffmpeg') or 'ffmpeg'
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


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
    if source['kind'] == 'youtube' and not project['transcript'] and not project.get('source_subtitles_checked'):
        report(93, 'Tìm phụ đề do kênh YouTube cung cấp…')
        from yt_dlp import YoutubeDL
        try:
            options = {'skip_download': True, 'writesubtitles': True, 'writeautomaticsub': False,
                       'subtitleslangs': ['en.*', 'vi.*'], 'subtitlesformat': 'srt',
                       'outtmpl': str(folder / 'captions.%(ext)s'), 'noplaylist': True,
                       'quiet': True, 'socket_timeout': 15, 'retries': 1, 'ignoreerrors': True}
            with YoutubeDL(options) as downloader:
                info = downloader.extract_info(source['url'], download=True) or {}
            check()
            files = sorted(folder.glob('captions.*.srt'))
            preferred = str(info.get('language') or 'en').split('-')[0]
            files.sort(key=lambda p: not p.name.startswith('captions.' + preferred))
            if files:
                project['transcript'] = parse_srt(files[0].read_text('utf-8-sig'))
                project['source_transcript'] = [dict(c) for c in project['transcript']]
                project['transcript_origin'] = 'youtube_subtitles'
                project['source_subtitles'] = files[0].name
        except Cancelled:
            raise
        except Exception:
            project['warnings'].append('Không lấy được phụ đề kênh; sẽ nhận dạng từ âm thanh nguồn.')
        project['source_subtitles_checked'] = True
    report(95, 'Nguồn đã sẵn sàng')
    return store.save(project)


_asr_cache = {}


def transcribe(path, settings, check=lambda: None, expected_text=None):
    from faster_whisper import WhisperModel
    key = (settings['asr_model'], settings['asr_device'])
    if key not in _asr_cache:
        _asr_cache.clear()
        _asr_cache[key] = WhisperModel(key[0], device=key[1], compute_type='int8' if key[1] == 'cpu' else 'float16', download_root=str(store.DATA / 'models'))
    language = {'Vietnamese': 'vi', 'English': 'en', 'Chinese': 'zh'}.get(settings['language']) if expected_text else None
    segments, info = _asr_cache[key].transcribe(str(path), word_timestamps=True, vad_filter=True, beam_size=5, language=language, initial_prompt=expected_text)
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
