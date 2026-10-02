"""Project-owned background music, independent of source sound and TTS."""
import json
import math
import uuid
from pathlib import Path

import av

from . import store

EXTENSIONS = {'.wav', '.mp3', '.m4a', '.ogg', '.flac'}
MAX_BYTES = 100 * 1024**2
ASSET_FIELDS = {'music_file', 'music_name', 'music_duration'}


def inspect(path):
    try:
        with av.open(str(path)) as container:
            audio = next(iter(container.streams.audio), None)
            if audio is None:
                raise ValueError('File nhạc không có âm thanh.')
            duration = (float(audio.duration * audio.time_base) if audio.duration is not None
                        else float((container.duration or 0) / av.time_base))
            frame = next(container.decode(audio=0), None)
            if frame is None or not math.isfinite(duration) or duration <= 0:
                raise ValueError('Không đọc được âm thanh/thời lượng nhạc.')
            return duration
    except (av.error.FFmpegError, OSError) as exc:
        raise ValueError('Không đọc được file nhạc. Chọn WAV, MP3, M4A, OGG hoặc FLAC hợp lệ.') from exc


def save_upload(folder, filename, content):
    ext = Path(filename or '').suffix.lower()
    if ext not in EXTENSIONS:
        raise ValueError('Chọn nhạc WAV, MP3, M4A, OGG hoặc FLAC.')
    if not content or len(content) > MAX_BYTES:
        raise ValueError('Nhạc nền phải có dữ liệu và tối đa 100 MB.')
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ('music-' + uuid.uuid4().hex + ext)
    try:
        path.write_bytes(content)
        duration = inspect(path)
        return path, {'music_file': path.name, 'music_name': Path(filename).name[:220],
                      'music_duration': duration}
    except Exception:
        path.unlink(missing_ok=True)
        raise


def stage(filename, content):
    path, metadata = save_upload(store.DATA / '_batch_music', filename, content)
    try:
        path.with_suffix(path.suffix + '.json').write_text(json.dumps(metadata), 'utf-8')
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {'token': path.name, 'name': metadata['music_name'], 'duration': metadata['music_duration']}


def staged(token):
    if (not token or Path(token).name != token or not token.startswith('music-')
            or len(Path(token).stem) != 38
            or any(c not in '0123456789abcdef' for c in Path(token).stem[6:])
            or Path(token).suffix.lower() not in EXTENSIONS):
        raise ValueError('Nhạc nền của lô không hợp lệ. Hãy tải lại file.')
    path = store.DATA / '_batch_music' / token
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
        raise ValueError('File nhạc nền của lô không còn tồn tại. Hãy tải lại file.')
    try:
        metadata = json.loads(path.with_suffix(path.suffix + '.json').read_text('utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('Thông tin nhạc nền của lô không còn hợp lệ. Hãy tải lại file.') from exc
    return path, {**metadata, 'music_file': path.name, 'music_duration': inspect(path)}


def project_asset(project):
    relative = project['settings'].get('music_file', '')
    if not relative:
        return None
    path = store.asset(project['id'], relative)
    if path.suffix.lower() not in EXTENSIONS or not path.is_file():
        raise ValueError('Không tìm thấy file nhạc nền. Tải lại nhạc hoặc bỏ track nhạc nền.')
    return path


def track(project, duration):
    s = project['settings']
    if not s.get('music_file'):
        return None
    return dict(audio=s['music_file'], name=s.get('music_name') or s['music_file'], start=0,
                end=duration, duration=s.get('music_duration', 0), volume=s.get('music_volume', .15), loop=True)


def append_mix(project, part, args, filters, mixed, input_index):
    """Loop on the completed timeline clock, preserving phase across exports."""
    path = project_asset(project)
    if path is None or project['settings'].get('music_volume', .15) == 0:
        return
    args.extend(['-stream_loop', '-1', '-i', str(path)])
    filters.append(f'[{input_index}:a:0]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS,'
                   f'atrim=start={part["start"]:.6f}:duration={part["duration"]:.6f},'
                   f'asetpts=PTS-STARTPTS,volume={project["settings"].get("music_volume", .15)},'
                   f'apad,atrim=duration={part["duration"]:.6f}[music]')
    mixed.append('[music]')
