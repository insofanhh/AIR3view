"""Publish completed renders to a user-selected local directory."""
import ctypes
import json
import os
import re
import shutil
import subprocess
import threading
import time
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path

from . import store

_FOLDER_LOCK = threading.RLock()
_PICKER_LOCK = threading.Lock()


def pick_directory(initial=''):
    """Show the Windows folder picker without requiring Tk in the embedded runtime."""
    if os.name != 'nt':
        raise ValueError('Chọn thư mục bằng File Explorer chỉ hỗ trợ Windows.')
    start = Path(initial) if initial and Path(initial).is_dir() else store.DATA / 'exports'
    if not start.is_dir():
        start = Path.home()
    script = r'''
Add-Type -AssemblyName System.Windows.Forms
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Chọn thư mục lưu video AIR3view'
$dialog.ShowNewFolderButton = $true
$dialog.SelectedPath = $env:AIR3VIEW_PICKER_INITIAL
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$owner.Opacity = 0
try {
    $owner.Show()
    if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
        [Console]::Out.Write($dialog.SelectedPath)
    }
} finally {
    $dialog.Dispose()
    $owner.Dispose()
}
'''
    environment = dict(os.environ, AIR3VIEW_PICKER_INITIAL=str(start))
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    if not _PICKER_LOCK.acquire(blocking=False):
        raise ValueError('Hộp thoại chọn thư mục đang mở. Hãy chọn hoặc đóng hộp thoại hiện tại.')
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-STA', '-NonInteractive', '-Command', script],
                                capture_output=True, text=True, encoding='utf-8', errors='replace',
                                env=environment, creationflags=flags, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('Không mở được hộp thoại chọn thư mục Windows.') from exc
    finally:
        _PICKER_LOCK.release()
    if result.returncode:
        raise ValueError('Không mở được hộp thoại chọn thư mục Windows: ' + result.stderr.strip()[-300:])
    selected = result.stdout.strip()
    if selected:
        export_root(directory=selected)
    return selected


def available_drives():
    if os.name != 'nt':
        return []
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    return [f'{chr(65 + index)}:' for index in range(26) if mask & (1 << index)
            and ctypes.windll.kernel32.GetDriveTypeW(f'{chr(65 + index)}:\\') in (2, 3)]


def storage_options():
    return {'default': str(store.DATA / 'exports'), 'drives': available_drives()}


def export_root(drive='', directory=''):
    if directory:
        path = Path(directory)
        if not path.is_absolute() or not path.is_dir():
            raise ValueError('Thư mục lưu video không hợp lệ hoặc không còn khả dụng. Hãy chọn lại trong File Explorer.')
        return path
    if not drive:
        return store.DATA / 'exports'
    if drive not in available_drives():
        raise ValueError('Ổ lưu video không còn khả dụng. Chọn lại ổ trong cài đặt xuất video.')
    return Path(drive + '\\') / 'AIR3view Exports'


def folder_name(value, date=None):
    normalized = unicodedata.normalize('NFKD', str(value))
    normalized = ''.join(c for c in normalized if not unicodedata.combining(c))
    stem = re.sub(r'[^\w-]+', '_', normalized, flags=re.UNICODE).strip('._-')[:80] or 'video'
    return f'{stem}_{date or datetime.now().strftime("%Y-%m-%d")}'


def _owned_folder(parent, name, owner, marker):
    parent.mkdir(parents=True, exist_ok=True)
    for suffix in ('', '_' + owner[:8], '_' + owner):
        path = parent / (name + suffix)
        manifest = path / marker
        if not path.exists():
            path.mkdir()
            return path
        if manifest.is_file():
            try:
                if json.loads(manifest.read_text('utf-8')).get('id') == owner:
                    return path
            except (OSError, ValueError):
                pass
    raise ValueError('Không tạo được thư mục xuất riêng cho dự án này.')


def batch_folder(batch_id, drive='', date=None, directory=''):
    with store.conn() as db:
        batch = db.execute('SELECT name FROM batches WHERE id=?', (batch_id,)).fetchone()
    if not batch:
        raise ValueError('Không tìm thấy lô video để xuất.')
    with _FOLDER_LOCK:
        path = _owned_folder(export_root(drive, directory), folder_name(batch['name'], date), batch_id, '.air3view-batch.json')
        (path / '.air3view-batch.json').write_text(json.dumps({'id': batch_id}), 'utf-8')
    return path


def publish(project, drive='', batch_id=None, check=lambda: None, date=None, directory=''):
    """Copy MP4 and both subtitle formats atomically; keep render cache intact."""
    parent = batch_folder(batch_id, drive, date, directory) if batch_id else export_root(drive, directory)
    with _FOLDER_LOCK:
        folder = _owned_folder(parent, folder_name(project['name'], date), project['id'], '.air3view-project.json')
    marker = folder / '.air3view-project.json'
    try:
        previous = json.loads(marker.read_text('utf-8')) if marker.is_file() else {}
    except (OSError, ValueError):
        previous = {}
    files = []
    identities = {}
    for export in project.get('exports') or []:
        for key in ('file', 'srt', 'ass'):
            relative = export.get(key)
            if not relative:
                continue
            source = store.asset(project['id'], relative)
            if not source.is_file():
                raise ValueError('Thiếu file bản dựng hoặc phụ đề: ' + key)
            name = source.name
            destination = folder / name
            temporary = folder / ('.' + name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                check()
                shutil.copy2(source, temporary)
                if temporary.stat().st_size != source.stat().st_size:
                    raise OSError('File xuất chưa sao chép đầy đủ.')
                check()
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
            files.append(name)
            stat = destination.stat()
            identities[name] = {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
    if not any(name.lower().endswith('.mp4') for name in files):
        raise ValueError('Dự án chưa có MP4 hoàn chỉnh để xuất.')
    temporary = folder / ('.air3view-project.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps({'id': project['id'], 'files': files,
                                         'identities': identities,
                                         'exported_at': time.time()}, ensure_ascii=False), 'utf-8')
        temporary.replace(marker)
    finally:
        temporary.unlink(missing_ok=True)
    if previous.get('id') == project['id']:
        for name in set(previous.get('files', [])) - set(files):
            if Path(name).name != name or Path(name).suffix.lower() not in ('.mp4', '.srt', '.ass'):
                continue
            stale = folder / name
            identity = previous.get('identities', {}).get(name)
            try:
                stat = stale.stat()
                if identity == {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}:
                    stale.unlink()
            except OSError:
                pass
    return {'folder': str(folder), 'files': files}
