from pathlib import Path
import os
import subprocess

import pytest

from installer.launcher import runtime_environment


def test_installed_runtime_uses_private_data_and_bundled_tools(tmp_path):
    root = tmp_path / 'AIR3view'
    (root / 'tools').mkdir(parents=True)
    (root / 'tools' / 'codex.exe').touch()
    env = runtime_environment(root, {'LOCALAPPDATA': str(tmp_path / 'user'), 'PATH': 'system-tools'})
    assert env['AIR3VIEW_DATA'] == str(tmp_path / 'user' / 'AIR3view' / 'data')
    assert env['FFMPEG_PATH'] == str(root / 'tools' / 'ffmpeg.exe')
    assert env['CODEX_PATH'] == str(root / 'tools' / 'codex.exe')
    assert env['PATH'].split(';')[0] == str(root / 'tools')
    assert env['PYTHONUTF8'] == '1'
    assert env['AIR3VIEW_LAUNCHER'] == '1'


def test_installed_runtime_respects_existing_data_folder(tmp_path):
    root = tmp_path / 'AIR3view'
    existing = tmp_path / 'old-projects'
    env = runtime_environment(root, {'AIR3VIEW_DATA': str(existing), 'FFMPEG_PATH': 'custom-ffmpeg'})
    assert env['AIR3VIEW_DATA'] == str(existing)
    assert env['FFMPEG_PATH'] == 'custom-ffmpeg'
    assert 'CODEX_PATH' not in env


@pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell compatibility')
def test_updater_script_runs_with_windows_powershell():
    helper = Path(__file__).resolve().parents[1] / 'installer' / 'update-helper.ps1'
    assert helper.read_bytes().startswith(b'\xef\xbb\xbf')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                             '-ExecutionPolicy', 'Bypass', '-File', str(helper), '-ValidateOnly'],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
