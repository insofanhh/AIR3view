"""Inhibit automatic Windows sleep only while a media job is running."""
import sys
from contextlib import contextmanager


@contextmanager
def keep_awake():
    previous = 0
    function = None
    if sys.platform == 'win32':
        import ctypes
        function = ctypes.windll.kernel32.SetThreadExecutionState
        function.argtypes = [ctypes.c_uint]
        function.restype = ctypes.c_uint
        # Keep computation running; do not force the display on or change
        # power settings. Explicit user sleep/lid actions are not overridden.
        previous = function(0x80000001)
    try:
        yield
    finally:
        if previous and function:
            function(previous)
