"""Smoke-test the vendored Python runtime and installed app without a model download."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen


def main():
    root = Path(sys.executable).resolve().parent.parent
    assert (root / 'frontend' / 'dist' / 'index.html').is_file()
    assert (root / 'tools' / 'ffmpeg.exe').is_file()
    assert (root / 'tools' / 'deno.exe').is_file()
    assert (root / 'tools' / 'codex.exe').is_file()
    assert (root / 'APP_VERSION').is_file()
    from backend import vieneu
    from backend.vieneu_onnx_files import install_sdk_fetch_hook

    assert vieneu.status()['ok'], 'VieNeu SDK missing from installer'
    with tempfile.TemporaryDirectory() as data:
        install_sdk_fetch_hook(Path(data))
        render = Path(data) / 'encoder-test.mp4'
        subprocess.run([str(root / 'tools' / 'ffmpeg.exe'), '-hide_banner', '-loglevel', 'error',
                        '-f', 'lavfi', '-i', 'color=size=64x64:rate=30:color=black',
                        '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=48000',
                        '-t', '0.2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                        '-y', str(render)], check=True, timeout=30)
        assert render.stat().st_size > 0, 'Bundled FFmpeg could not encode H.264/AAC'
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = dict(os.environ, AIR3VIEW_DATA=data, AIR3VIEW_PORT=str(port),
                   FFMPEG_PATH=str(root / 'tools' / 'ffmpeg.exe'),
                   CODEX_PATH=str(root / 'tools' / 'codex.exe'),
                   PATH=str(root / 'tools') + os.pathsep + os.environ.get('PATH', ''))
        with (root / 'smoke-server.log').open('wb') as log:
            process = subprocess.Popen([sys.executable, str(root / 'run.py')], cwd=root, env=env,
                                       stdout=log, stderr=log,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError('Packaged server exited during startup')
                    try:
                        with urlopen(f'http://127.0.0.1:{port}/api/health', timeout=1) as response:
                            health = json.load(response)
                        break
                    except OSError:
                        time.sleep(.25)
                else:
                    raise TimeoutError('Packaged server did not become healthy')
                assert health['ok'] and health['ffmpeg'] and health['codex'], health
                assert health['version'] == (root / 'APP_VERSION').read_text(encoding='ascii').strip()
                with urlopen(f'http://127.0.0.1:{port}/api/tts?provider=vieneu', timeout=5) as response:
                    assert json.load(response)['ok']
                with urlopen(f'http://127.0.0.1:{port}/', timeout=5) as response:
                    assert b'AIR3view' in response.read()
            finally:
                process.terminate()
                process.wait(timeout=15)
    print('Packaged AIR3view HTTP, FFmpeg, Codex, VieNeu and frontend smoke passed.')


if __name__ == '__main__':
    main()
