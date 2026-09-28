"""Check published Windows installers without slowing normal local requests."""

import re
import threading
import time
from pathlib import Path

import requests


VERSION_FILE = Path(__file__).resolve().parent.parent / 'APP_VERSION'
API_URL = 'https://api.github.com/repos/insofanhh/AIR3view/releases/latest'
RELEASES_URL = 'https://github.com/insofanhh/AIR3view/releases'
CACHE_SECONDS = 6 * 60 * 60
ERROR_CACHE_SECONDS = 10 * 60
_lock = threading.Lock()
_cache = {'until': 0.0, 'value': None}


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
    assets = data.get('assets', [])
    installer = next((asset for asset in assets if isinstance(asset, dict)
                      and asset.get('name') == f'AIR3view-Setup-{tag.removeprefix("v")}-win64.exe'
                      and asset.get('state') == 'uploaded'), None) if isinstance(assets, list) else None
    if installer is None:
        raise ValueError('Windows installer has not been uploaded')
    return {'available': latest > version_tuple(current), 'status': 'ok',
            'current_version': current, 'latest_version': tag.removeprefix('v'),
            'release_url': release_url}


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
        except (requests.RequestException, ValueError, TypeError, KeyError):
            result = {'available': False, 'status': 'unavailable', 'current_version': current,
                      'latest_version': None, 'release_url': None}
            lifetime = ERROR_CACHE_SECONDS
        _cache.update(until=time.monotonic() + lifetime, value=result)
        return dict(result)
