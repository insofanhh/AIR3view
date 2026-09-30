"""Machine-local, opt-in authentication for YouTube downloads.

Cookie contents never enter a project, batch row, API response or log.
"""
import json
import os
import shutil
import subprocess
import uuid
from functools import lru_cache
from pathlib import Path

from . import store

MAX_COOKIE_BYTES = 2 * 1024 * 1024
MODES = {'none', 'chrome', 'edge', 'file'}


def _directory():
    path = store.DATA / '_youtube'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _config_path():
    return _directory() / 'auth.json'


def _cookie_path():
    return _directory() / 'cookies.txt'


def _config():
    try:
        result = json.loads(_config_path().read_text(encoding='utf-8'))
        if result.get('mode') in MODES:
            return result
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return {'mode': 'none', 'revision': ''}


def status():
    config = _config()
    return {'mode': config['mode'], 'has_cookie_file': _cookie_path().is_file()}


def revision():
    config = _config()
    return config.get('revision', '')


def set_mode(mode):
    if mode not in MODES:
        raise ValueError('Cách xác thực YouTube không hợp lệ.')
    if mode == 'file' and not _cookie_path().is_file():
        raise ValueError('Hãy tải lên cookies.txt trước khi chọn chế độ file.')
    config = {'mode': mode, 'revision': uuid.uuid4().hex}
    temporary = _config_path().with_suffix('.tmp')
    temporary.write_text(json.dumps(config), encoding='utf-8')
    temporary.replace(_config_path())
    return status()


def save_cookie_file(content):
    if len(content) > MAX_COOKIE_BYTES:
        raise ValueError('File cookies.txt vượt quá 2 MB.')
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ValueError('cookies.txt phải là file văn bản UTF-8 theo định dạng Netscape.') from None
    lines = text.splitlines()
    if not lines or not any(line.startswith(('# Netscape HTTP Cookie File', '# HTTP Cookie File')) for line in lines[:3]):
        raise ValueError('File cookie phải dùng định dạng Netscape cookies.txt.')
    if not any(line.removeprefix('#HttpOnly_').startswith(('.youtube.com\t', 'youtube.com\t')) for line in lines):
        raise ValueError('cookies.txt chưa chứa cookie của youtube.com.')
    # yt-dlp expects the platform's newline convention and a header without BOM.
    normalized = ('\r\n' if os.name == 'nt' else '\n').join(lines) + ('\r\n' if os.name == 'nt' else '\n')
    content = normalized.encode('utf-8')
    if len(content) > MAX_COOKIE_BYTES:
        raise ValueError('File cookies.txt vượt quá 2 MB.')
    path = _cookie_path()
    temporary = path.with_name('cookies-' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(content)
        if os.name != 'nt':
            temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return set_mode('file')


def options():
    mode = _config()['mode']
    if mode == 'none':
        return {}
    if mode == 'file':
        if not _cookie_path().is_file():
            raise RuntimeError('Thiếu cookies.txt. Vào Kết nối → YouTube để tải file mới.')
        return {'cookiefile': str(_cookie_path())}
    return {'cookiesfrombrowser': (mode,)}


@lru_cache(maxsize=8)
def _runtime_version(path):
    try:
        result = subprocess.run([path, '--version'], capture_output=True, text=True, timeout=4)
        if result.returncode == 0:
            return result.stdout.strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        pass
    return ''


def runtime_options():
    """Use bundled Deno, or a compatible Node, for yt-dlp's YouTube EJS solver."""
    local_deno = store.DATA / '_runtime' / 'deno.exe'
    for path in (str(local_deno) if local_deno.is_file() else None, shutil.which('deno')):
        version = _runtime_version(path).split() if path else []
        number = version[1].split('.') if len(version) > 1 and version[0] == 'deno' else []
        if len(number) >= 2 and all(part.isdigit() for part in number[:2]) and (int(number[0]), int(number[1])) >= (2, 3):
            return {'js_runtimes': {'deno': {'path': path}}}
    for path in (shutil.which('node'),):
        if path:
            version = _runtime_version(path).lstrip('v').split('.')
            if version and version[0].isdigit() and int(version[0]) >= 22:
                return {'js_runtimes': {'node': {'path': path}}}
    return {}


def friendly_error(error):
    message = str(error).lower()
    if 'sign in to confirm you' in message and 'bot' in message:
        return ('YouTube yêu cầu xác minh phiên tải trên máy này. Vào Kết nối → YouTube, '
                'chọn Chrome/Edge đã đăng nhập hoặc tải cookies.txt, rồi bấm Thử lại. '
                'Nếu vẫn bị chặn, nhập video bằng file trên máy.')
    if 'requested format is not available' in message and not runtime_options():
        return ('YouTube không trả về định dạng video vì máy thiếu Deno hoặc Node.js 22+ '
                'để giải thử thách JavaScript. Cài Deno, khởi động lại AIR3view rồi thử lại.')
    if 'winerror 10013' in message or 'forbidden by its access permissions' in message:
        return ('AIR3view không được phép kết nối ra YouTube qua HTTPS (Windows socket 10013). '
                'Hãy khởi động ứng dụng trong phiên Windows bình thường và kiểm tra firewall; '
                'file cookie không gây ra lỗi mạng này.')
    if any(term in message for term in ('could not copy chrome cookie', 'could not copy edge cookie',
                                         'could not find chrome cookies database',
                                         'could not find edge cookies database',
                                         'could not decrypt', 'failed to decrypt',
                                         'failed to load cookies', 'permission denied',
                                         'database is locked')) and _config()['mode'] in ('chrome', 'edge'):
        return ('Không đọc được cookie trình duyệt đã chọn (có thể Chrome/Edge đang khóa file hoặc '
                'AIR3view chạy bằng tài khoản Windows khác). Hãy đóng trình duyệt rồi thử lại; '
                'nếu đang mở AIR3view trong trình duyệt đó, hãy dùng file cookies.txt '
                'ở Kết nối → YouTube hoặc mở AIR3view bằng trình duyệt khác.')
    return None
