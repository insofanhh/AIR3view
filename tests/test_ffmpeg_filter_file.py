from types import SimpleNamespace

import pytest

from backend import media


@pytest.mark.parametrize('supported,expected_calls', [
    ('-/filter_complex', ['-/filter_complex']),
    ('-filter_complex_script', ['-/filter_complex', '-filter_complex_script']),
])
def test_filter_file_option_detects_current_binary_and_caches_result(monkeypatch, supported, expected_calls):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args[4])
        assert args[-5:] == ['-map', '[outa]', '-f', 'null', '-']
        assert kwargs['timeout'] == 10
        return SimpleNamespace(returncode=0 if args[4] == supported else 1)

    media.filter_complex_file_option.cache_clear()
    monkeypatch.setattr(media.subprocess, 'run', fake_run)
    assert media.filter_complex_file_args('scene.filters.txt', 'test-ffmpeg') == [supported, 'scene.filters.txt']
    assert media.filter_complex_file_args('audio.filters.txt', 'test-ffmpeg') == [supported, 'audio.filters.txt']
    assert calls == expected_calls
    media.filter_complex_file_option.cache_clear()


def test_filter_file_option_rejects_binary_with_no_supported_file_syntax(monkeypatch):
    media.filter_complex_file_option.cache_clear()
    monkeypatch.setattr(media.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=1))
    with pytest.raises(RuntimeError, match='không đọc được filter graph'):
        media.filter_complex_file_args('scene.filters.txt', 'broken-ffmpeg')
    media.filter_complex_file_option.cache_clear()
