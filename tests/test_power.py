import ctypes
from types import SimpleNamespace
import pytest
from backend import power


def test_failed_job_restores_previous_sleep_state(monkeypatch):
    calls = []
    def change(flags):
        calls.append(flags)
        return 0x80000000
    monkeypatch.setattr(power.sys, 'platform', 'win32')
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(kernel32=SimpleNamespace(SetThreadExecutionState=change)), raising=False)
    with pytest.raises(RuntimeError, match='job failed'):
        with power.keep_awake():
            raise RuntimeError('job failed')
    assert calls == [0x80000001, 0x80000000]
