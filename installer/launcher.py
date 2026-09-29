"""Start the installed local server and keep a small Windows tray control."""

import ctypes
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time
from urllib.request import urlopen
import webbrowser


def runtime_environment(root: Path, source: dict[str, str] | None = None) -> dict[str, str]:
    """Keep project files outside the install directory and use bundled tools."""
    env = dict(os.environ if source is None else source)
    tools = root / 'tools'
    local = Path(env.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    env.setdefault('AIR3VIEW_DATA', str(local / 'AIR3view' / 'data'))
    env.setdefault('FFMPEG_PATH', str(tools / 'ffmpeg.exe'))
    if (tools / 'codex.exe').is_file():
        env.setdefault('CODEX_PATH', str(tools / 'codex.exe'))
    env['PATH'] = str(tools) + os.pathsep + env.get('PATH', '')
    env['PYTHONUTF8'] = '1'
    env['AIR3VIEW_LAUNCHER'] = '1'
    return env


def healthy(url: str) -> bool:
    try:
        with urlopen(url + '/api/health', timeout=.6) as response:
            status = json.load(response)
        return status.get('ok') is True and 'voice_repair_version' in status
    except (OSError, ValueError):
        return False


def wait_ready(url: str, seconds: int, worker: threading.Thread | None = None) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if healthy(url):
            return True
        if worker is not None and not worker.is_alive():
            return False
        time.sleep(.25)
    return False


def message(text: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, 'AIR3view', 0x10)


def tray_image():
    from PIL import Image, ImageDraw

    image = Image.new('RGB', (64, 64), '#171b23')
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((3, 3, 60, 60), radius=13, fill='#b9f46a')
    draw.polygon(((22, 15), (49, 32), (22, 49)), fill='#171b23')
    return image


def main() -> None:
    if os.name != 'nt':
        raise RuntimeError('AIR3view installer chỉ hỗ trợ Windows.')
    root = Path(__file__).resolve().parent
    os.environ.update(runtime_environment(root))
    data = Path(os.environ['AIR3VIEW_DATA'])
    data.mkdir(parents=True, exist_ok=True)
    log = (data / 'launcher.log').open('a', encoding='utf-8', buffering=1)
    sys.stdout = log
    sys.stderr = log
    logging.basicConfig(stream=log, level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    port = int(os.environ.get('AIR3VIEW_PORT', '8765'))
    if not 1 <= port <= 65535:
        raise ValueError('AIR3VIEW_PORT không hợp lệ.')
    url = f'http://127.0.0.1:{port}'

    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    mutex = kernel32.CreateMutexW(None, False, f'Local\\AIR3viewStudio-{port}')
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    already_running = ctypes.get_last_error() == 183
    try:
        if already_running:
            if wait_ready(url, 30):
                webbrowser.open(url)
            else:
                message('AIR3view đang khởi động nhưng chưa phản hồi. Xem launcher.log trong thư mục dữ liệu.')
            return

        import pystray
        import uvicorn

        server = uvicorn.Server(uvicorn.Config('backend.app:app', host='127.0.0.1',
                                               port=port, workers=1, access_log=False))

        def serve():
            try:
                server.run()
            except BaseException:
                logging.exception('Không thể chạy AIR3view')

        worker = threading.Thread(target=serve, name='air3view-server', daemon=True)
        worker.start()
        if not wait_ready(url, 45, worker):
            server.should_exit = True
            message('Không khởi động được AIR3view. Có thể cổng đã được ứng dụng khác sử dụng. Xem launcher.log trong thư mục dữ liệu.')
            return
        webbrowser.open(url)

        def open_app(icon, item):
            webbrowser.open(url)

        def quit_app(icon, item):
            server.should_exit = True
            icon.stop()

        icon = pystray.Icon('AIR3view', tray_image(), 'AIR3view',
                            pystray.Menu(pystray.MenuItem('Mở AIR3view', open_app, default=True),
                                         pystray.MenuItem('Thoát', quit_app)))
        def watch_update():
            from backend.updates import INSTALL_REQUESTED
            while worker.is_alive() and not server.should_exit:
                if INSTALL_REQUESTED.wait(timeout=.25):
                    # Let the POST response reach the browser before closing.
                    time.sleep(.6)
                    server.should_exit = True
                    icon.stop()
                    return
        threading.Thread(target=watch_update, name='air3view-update-watcher', daemon=True).start()
        try:
            icon.run()
        finally:
            server.should_exit = True
            worker.join(timeout=10)
    finally:
        kernel32.CloseHandle(mutex)
        log.close()


if __name__ == '__main__':
    try:
        main()
    except BaseException as exc:
        logging.exception('Lỗi khởi động AIR3view')
        message(f'Không thể mở AIR3view: {exc}\nXem launcher.log trong thư mục dữ liệu.')
        raise
