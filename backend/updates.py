"""Check published Windows installers without slowing normal local requests."""

import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import threading
import time
from xml.etree import ElementTree
from pathlib import Path
from urllib.parse import urlparse

import requests


VERSION_FILE = Path(__file__).resolve().parent.parent / 'APP_VERSION'
API_URL = 'https://api.github.com/repos/insofanhh/AIR3view/releases/latest'
RELEASES_URL = 'https://github.com/insofanhh/AIR3view/releases'
RELEASES_FEED_URL = RELEASES_URL + '.atom'
CACHE_SECONDS = 6 * 60 * 60
ERROR_CACHE_SECONDS = 10 * 60
_lock = threading.Lock()
_cache = {'until': 0.0, 'value': None}
_download_lock = threading.RLock()
_download_state = {'phase': 'idle', 'version': '', 'downloaded': 0, 'total': 0, 'error': ''}
INSTALL_REQUESTED = threading.Event()
INSTALL_GUARD = threading.Event()


def install_supported() -> bool:
    return (os.name == 'nt' and os.environ.get('AIR3VIEW_LAUNCHER') == '1'
            and VERSION_FILE.is_file() and (VERSION_FILE.parent / 'update-helper.ps1').is_file())


def _update_dir() -> Path:
    from . import store
    return store.DATA / '_updates'


def _set_download(**changes) -> dict:
    with _download_lock:
        _download_state.update(changes)
        return dict(_download_state)


def download_status() -> dict:
    with _download_lock:
        state = dict(_download_state)
    # A previous installer can leave a diagnostic for the next launch.
    if state['phase'] == 'idle':
        path = _update_dir() / 'result.json'
        try:
            result = json.loads(path.read_text('utf-8'))
            if result.get('status') == 'failed':
                state.update(phase='error', error=result.get('message', 'Cập nhật thất bại.'))
        except (OSError, ValueError):
            pass
    return state


def _installer_asset(data: dict, tag: str) -> dict:
    assets = data.get('assets', [])
    name = f'AIR3view-Setup-{tag.removeprefix("v")}-win64.exe'
    asset = next((item for item in assets if isinstance(item, dict) and
                  item.get('name') == name and item.get('state') == 'uploaded'), None) if isinstance(assets, list) else None
    if asset is None:
        raise ValueError('Windows installer has not been uploaded')
    return asset


def version_tuple(value: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', value.strip())
    return tuple(map(int, match.groups())) if match else None


def installed_version() -> str:
    try:
        version = VERSION_FILE.read_text(encoding='utf-8').strip()
    except OSError:
        version = ''
    return version if version_tuple(version) is not None else '0.1.3'


def _published_release(data: dict, current: str) -> dict:
    if not isinstance(data, dict):
        raise ValueError('Invalid GitHub release response')
    tag = data.get('tag_name', '')
    latest = version_tuple(tag) if isinstance(tag, str) else None
    release_url = data.get('html_url', '')
    if (latest is None or data.get('draft') or data.get('prerelease')
            or not isinstance(release_url, str)
            or not release_url.startswith(RELEASES_URL + '/tag/')):
        raise ValueError('Invalid GitHub release metadata')
    installer = _installer_asset(data, tag)
    digest = installer.get('digest', '')
    size = installer.get('size', 0)
    verified_asset = (isinstance(digest, str) and re.fullmatch(r'sha256:[0-9a-fA-F]{64}', digest)
                      and isinstance(size, int) and 0 < size <= 2 * 1024**3)
    return {'available': latest > version_tuple(current), 'status': 'ok',
            'current_version': current, 'latest_version': tag.removeprefix('v'),
            'release_url': release_url, 'install_supported': install_supported() and bool(verified_asset),
            'installer_size': size if verified_asset else None,
            'installer_sha256': digest[7:].lower() if verified_asset else None}


def _web_release(current: str) -> dict:
    """Use GitHub's public release pages when its unauthenticated API is limited.

    The feed identifies the release. The installer is offered in-app only when
    GitHub also publishes its SHA-256 and the download reports an exact size.
    """
    headers = {'User-Agent': 'AIR3view-update-check'}
    feed = requests.get(RELEASES_FEED_URL, headers=headers, timeout=(3, 8))
    feed.raise_for_status()
    if len(feed.content) > 512 * 1024:
        raise ValueError('GitHub release feed is too large')
    root = ElementTree.fromstring(feed.content)
    entries = root.findall('{http://www.w3.org/2005/Atom}entry')
    candidates = []
    for entry in entries:
        link = entry.find('{http://www.w3.org/2005/Atom}link')
        url = link.get('href', '') if link is not None else ''
        prefix = RELEASES_URL + '/tag/'
        tag = url[len(prefix):] if url.startswith(prefix) else ''
        if version_tuple(tag) is not None and url == prefix + tag:
            candidates.append((version_tuple(tag), tag, url))
    if not candidates:
        raise ValueError('No published version in GitHub release feed')
    _, tag, release_url = max(candidates)
    version = tag.removeprefix('v')
    result = {'available': version_tuple(tag) > version_tuple(current), 'status': 'ok',
              'current_version': current, 'latest_version': version,
              'release_url': release_url, 'install_supported': False,
              'installer_size': None, 'installer_sha256': None}
    if not result['available'] or not install_supported():
        return result
    try:
        asset_path = f'/insofanhh/AIR3view/releases/download/{tag}/AIR3view-Setup-{version}-win64.exe'
        assets = requests.get(RELEASES_URL + '/expanded_assets/' + tag,
                              headers=headers, timeout=(3, 8))
        assets.raise_for_status()
        if len(assets.content) > 512 * 1024:
            return result
        page = html.unescape(assets.text)
        marker = 'href="' + asset_path + '"'
        position = page.find(marker)
        if position < 0:
            return result
        tail = page[position + len(marker):position + len(marker) + 7000]
        next_asset = tail.find('releases/download/')
        if next_asset >= 0:
            tail = tail[:next_asset]
        digest = re.search(r'sha256:([0-9a-fA-F]{64})', tail)
        if not digest:
            return result
        download = requests.get('https://github.com' + asset_path, headers=headers,
                                stream=True, timeout=(3, 10))
        try:
            download.raise_for_status()
            host = urlparse(download.url).hostname or ''
            size = int(download.headers.get('Content-Length', '0'))
            if (host == 'github.com' or host.endswith('.githubusercontent.com')) and 0 < size <= 2 * 1024**3:
                result.update(install_supported=True, installer_size=size,
                              installer_sha256=digest.group(1).lower())
        finally:
            download.close()
    except (requests.RequestException, ValueError, TypeError, OverflowError):
        pass  # Still show the published release link without offering an unverified installer.
    return result


def check_updates(refresh: bool = False) -> dict:
    """Cache both success and failure; never make connectivity a startup requirement."""
    current = installed_version()
    now = time.monotonic()
    with _lock:
        if not refresh and _cache['value'] is not None and now < _cache['until']:
            return dict(_cache['value'], current_version=current,
                        available=bool(_cache['value']['latest_version']
                                       and version_tuple(_cache['value']['latest_version']) > version_tuple(current)))
        try:
            response = requests.get(API_URL, headers={'Accept': 'application/vnd.github+json',
                                                       'User-Agent': 'AIR3view-update-check'},
                                    timeout=(2, 4))
            response.raise_for_status()
            result = _published_release(response.json(), current)
            lifetime = CACHE_SECONDS
        except (requests.RequestException, ValueError, TypeError, KeyError) as error:
            try:
                result = _web_release(current)
                lifetime = CACHE_SECONDS
            except (requests.RequestException, ValueError, TypeError, ElementTree.ParseError):
                code = getattr(getattr(error, 'response', None), 'status_code', None)
                reason = 'rate_limited' if code in (403, 429) else 'connection_failed'
                result = {'available': False, 'status': 'unavailable', 'reason': reason,
                          'current_version': current, 'latest_version': None, 'release_url': None,
                          'install_supported': False, 'installer_size': None, 'installer_sha256': None}
                lifetime = ERROR_CACHE_SECONDS
        _cache.update(until=time.monotonic() + lifetime, value=result)
        return dict(result)


def _download(version: str, size: int, digest: str) -> None:
    directory = _update_dir()
    filename = f'AIR3view-Setup-{version}-win64.exe'
    partial = directory / (filename + '.part')
    destination = directory / filename
    url = f'{RELEASES_URL}/download/v{version}/{filename}'
    try:
        directory.mkdir(parents=True, exist_ok=True)
        if destination.is_file() and destination.stat().st_size == size and _sha256(destination) == digest:
            _set_download(phase='ready', downloaded=size)
            return
        offset = partial.stat().st_size if partial.is_file() else 0
        if offset >= size:
            partial.unlink(missing_ok=True)
            offset = 0
        headers = {'User-Agent': 'AIR3view-update-download'}
        if offset:
            headers['Range'] = f'bytes={offset}-'
        with requests.get(url, headers=headers, stream=True, timeout=(8, 40)) as response:
            response.raise_for_status()
            host = urlparse(response.url).hostname or ''
            if host != 'github.com' and not host.endswith('.githubusercontent.com'):
                raise ValueError('Máy chủ tải bộ cài không thuộc GitHub.')
            if offset and (response.status_code != 206 or
                           not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-')):
                offset = 0
            with partial.open('ab' if offset else 'wb') as output:
                downloaded = offset
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        downloaded += len(chunk)
                        if downloaded > size:
                            raise ValueError('Bộ cài tải về lớn hơn dung lượng đã công bố.')
                        output.write(chunk)
                        _set_download(downloaded=downloaded)
        if partial.stat().st_size != size or _sha256(partial) != digest:
            partial.unlink(missing_ok=True)
            raise ValueError('Bộ cài tải về không khớp SHA-256 trên GitHub. Hãy thử lại.')
        partial.replace(destination)
        _set_download(phase='ready', downloaded=size)
    except (OSError, requests.RequestException, ValueError) as error:
        _set_download(phase='error', error=str(error))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def start_download() -> dict:
    if not install_supported():
        raise ValueError('Bản AIR3view này chưa hỗ trợ cập nhật trong ứng dụng. Hãy cài bản mới một lần từ GitHub.')
    release = check_updates(refresh=True)
    if not release['available'] or not release['installer_sha256']:
        raise ValueError('Chưa có bản cập nhật đã xác minh để tải.')
    with _download_lock:
        if _download_state['phase'] == 'downloading':
            return dict(_download_state)
        _download_state.update(phase='downloading', version=release['latest_version'],
                               downloaded=0, total=release['installer_size'], error='')
        worker = threading.Thread(target=_download, args=(release['latest_version'],
                                  release['installer_size'], release['installer_sha256']), daemon=True,
                                  name='air3view-update-download')
        worker.start()
        return dict(_download_state)


def _active_work() -> bool:
    from . import store
    with store.conn() as db:
        jobs = db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone()
        batches = db.execute("SELECT 1 FROM batches WHERE state IN ('running','paused') LIMIT 1").fetchone()
    return bool(jobs or batches)


def start_install() -> dict:
    if not install_supported():
        raise ValueError('Chỉ bản AIR3view đã cài trên Windows mới có thể tự cập nhật.')
    with _download_lock:
        state = dict(_download_state)
        if state['phase'] != 'ready':
            raise ValueError('Bộ cài chưa tải xong và xác minh SHA-256.')
        INSTALL_GUARD.set()
        try:
            if _active_work():
                raise ValueError('Đang có video hoặc lô chưa hoàn tất. Hãy đợi xử lý xong rồi cập nhật.')
            version = state['version']
            release = check_updates()
            if release['latest_version'] != version or not release['available']:
                raise ValueError('Phiên bản tải về không còn là bản cập nhật hiện hành.')
            installer = _update_dir() / f'AIR3view-Setup-{version}-win64.exe'
            if not installer.is_file() or installer.stat().st_size != release['installer_size'] or _sha256(installer) != release['installer_sha256']:
                raise ValueError('Bộ cài đã thay đổi hoặc không còn hợp lệ. Hãy tải lại.')
            helper = _update_dir() / 'update-helper.ps1'
            shutil.copy2(VERSION_FILE.parent / 'update-helper.ps1', helper)
            (_update_dir() / 'result.json').unlink(missing_ok=True)
            environment = dict(os.environ)
            environment.update(AIR3VIEW_UPDATE_INSTALLER=str(installer),
                               AIR3VIEW_UPDATE_ROOT=str(VERSION_FILE.parent),
                               AIR3VIEW_UPDATE_DATA=str(_update_dir()),
                               AIR3VIEW_UPDATE_VERSION=version,
                               AIR3VIEW_UPDATE_PID=str(os.getpid()),
                               AIR3VIEW_UPDATE_PORT=environment.get('AIR3VIEW_PORT', '8765'))
            # Capture PowerShell startup and parse errors, which happen before
            # the helper can write result.json or the installer log.
            with (_update_dir() / 'helper-launch.log').open('ab') as helper_log:
                subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                                  '-WindowStyle', 'Hidden', '-File', str(helper)],
                                 env=environment, cwd=str(_update_dir()), close_fds=True,
                                 stdout=helper_log, stderr=subprocess.STDOUT,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError as error:
            INSTALL_GUARD.clear()
            raise ValueError('Không chuẩn bị hoặc khởi chạy được trình cập nhật Windows.') from error
        except Exception:
            INSTALL_GUARD.clear()
            raise
        _download_state['phase'] = 'installing'
        INSTALL_REQUESTED.set()
        return dict(_download_state)
