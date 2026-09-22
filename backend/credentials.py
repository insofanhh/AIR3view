"""Persist API credentials encrypted for the current Windows user.

The credential file deliberately lives outside project directories.  Each
provider value is protected separately so replacing or deleting one provider
does not expose or rewrite another provider's plaintext.
"""

import base64
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

from . import store


_PROVIDERS = frozenset({'openai', 'gemini'})
_LOCK = threading.RLock()
_VERSION = 1


def _provider(provider):
    value = str(provider).strip().lower()
    if value not in _PROVIDERS:
        raise ValueError('Nhà cung cấp API không hợp lệ.')
    return value


def _path():
    return store.DATA / '.private' / 'credentials.json'


def _read():
    path = _path()
    if not path.is_file():
        return {'version': _VERSION, 'keys': {}}
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError('Không thể đọc kho API key đã mã hóa.') from exc
    if (not isinstance(payload, dict) or payload.get('version') != _VERSION
            or not isinstance(payload.get('keys'), dict)):
        raise RuntimeError('Định dạng kho API key không được hỗ trợ.')
    return payload


def _write(payload):
    try:
        _write_unchecked(payload)
    except OSError as exc:
        raise RuntimeError('Không thể lưu kho API key đã mã hóa.') from exc


def _write_unchecked(payload):
    path = _path()
    if not payload['keys']:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    handle, temporary_name = tempfile.mkstemp(prefix='.credentials-', suffix='.tmp', dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8', newline='') as stream:
            json.dump(payload, stream, ensure_ascii=True, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def _protect(value):
    if sys.platform != 'win32':
        raise RuntimeError('Lưu API key an toàn chỉ được hỗ trợ trên Windows.')
    try:
        return _cryptprotect(value.encode('utf-8'))
    except OSError as exc:
        raise RuntimeError('Không thể mã hóa API key cho tài khoản Windows này.') from exc


def _unprotect(value):
    if sys.platform != 'win32':
        raise RuntimeError('Đọc API key đã lưu chỉ được hỗ trợ trên Windows.')
    try:
        clear = _cryptunprotect(value)
        return clear.decode('utf-8')
    except (OSError, UnicodeDecodeError) as exc:
        raise RuntimeError('API key đã lưu không hợp lệ.') from exc


def _cryptprotect(clear):
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(clear)
    source = DATA_BLOB(len(clear), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = DATA_BLOB()
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR,
                                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                         wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    try:
        if not crypt32.CryptProtectData(ctypes.byref(source), 'AIR3view API key', None, None, None,
                                        0x1, ctypes.byref(result)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(result.pbData, result.cbData)
        finally:
            kernel32.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))
    finally:
        ctypes.memset(buffer, 0, len(buffer))


def _cryptunprotect(protected):
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(protected)
    source = DATA_BLOB(len(protected), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = DATA_BLOB()
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.c_void_p,
                                           ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                           wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None,
                                      0x1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.memset(result.pbData, 0, result.cbData)
        kernel32.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))
        ctypes.memset(buffer, 0, len(buffer))


def set_key(provider, value):
    """Encrypt and persist a provider key; an empty value deletes it."""
    provider = _provider(provider)
    value = str(value or '').strip()
    if not value:
        delete_key(provider)
        return
    protected = base64.b64encode(_protect(value)).decode('ascii')
    with _LOCK:
        payload = _read()
        payload['keys'][provider] = protected
        _write(payload)


def get_key(provider):
    """Return one provider's decrypted key, or an empty string when absent."""
    provider = _provider(provider)
    with _LOCK:
        encoded = _read()['keys'].get(provider)
    if not encoded:
        return ''
    try:
        protected = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise RuntimeError('API key đã lưu không hợp lệ.') from exc
    return _unprotect(protected)


def delete_key(provider):
    provider = _provider(provider)
    with _LOCK:
        payload = _read()
        if provider in payload['keys']:
            del payload['keys'][provider]
            _write(payload)


def stored_values():
    """Return decryptable stored secrets for error redaction.

    Redaction must never obscure an original application error because the
    credential store itself is unavailable or damaged.
    """
    values = []
    for provider in _PROVIDERS:
        try:
            value = get_key(provider)
        except (OSError, RuntimeError):
            continue
        if value:
            values.append(value)
    return tuple(values)
