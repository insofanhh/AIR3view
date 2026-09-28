from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from backend import vieneu_onnx_files as files
from backend import vieneu_worker


def test_materialized_graph_and_external_data_share_real_directory(tmp_path, monkeypatch):
    blobs = tmp_path / 'hub' / 'blobs'
    graph = blobs / '8b' / 'graph'
    data = blobs / '5c' / 'weights'
    graph.parent.mkdir(parents=True)
    data.parent.mkdir(parents=True)
    graph.write_bytes(b'onnx graph')
    data.write_bytes(b'external weights')
    sources = {'model.onnx': graph, 'model.data': data}
    calls = []

    def cached(repo, name, subfolder):
        calls.append(name)
        return sources[name]

    monkeypatch.setattr(files, '_cached_or_download', cached)
    target = files.materialized_fetch('repo/model', list(sources), 'onnx_update', tmp_path / 'app')
    assert (target / 'model.onnx').read_bytes() == graph.read_bytes()
    assert (target / 'model.data').read_bytes() == data.read_bytes()
    assert all(not (target / name).is_symlink() and (target / name).resolve().parent == target
               for name in sources)
    assert not list(target.glob('*.tmp'))
    assert files.materialized_fetch('repo/model', list(sources), 'onnx_update', tmp_path / 'app') == target
    assert calls == list(sources)  # no re-download or re-copy after first success


def test_missing_sidecar_retries_only_that_file(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.write_bytes(b'valid data')
    available = {'model.onnx'}
    calls = []

    def cached(repo, name, subfolder):
        calls.append(name)
        if name not in available:
            raise OSError('download interrupted')
        return source

    monkeypatch.setattr(files, '_cached_or_download', cached)
    with pytest.raises(OSError, match='interrupted'):
        files.materialized_fetch('repo/model', ['model.onnx', 'model.data'], None, tmp_path)
    available.add('model.data')
    target = files.materialized_fetch('repo/model', ['model.onnx', 'model.data'], None, tmp_path)
    assert calls == ['model.onnx', 'model.data', 'model.data']
    assert (target / 'model.data').read_bytes() == b'valid data'


def test_cpu_worker_installs_safe_fetch_before_loading_sdk(monkeypatch, tmp_path):
    observed = []
    monkeypatch.setattr(vieneu_worker, 'select_device', lambda requested: ('cpu', ''))
    monkeypatch.setattr(files, 'install_sdk_fetch_hook', lambda data: observed.append(('hook', data)))
    monkeypatch.setitem(sys.modules, 'vieneu', SimpleNamespace(Vieneu=lambda **kw: observed.append(('load', kw)) or object()))
    model, device, warning = vieneu_worker.load_model('cpu')
    assert device == 'cpu' and warning == ''
    assert [step[0] for step in observed] == ['hook', 'load']


def test_sdk_fetch_hook_covers_backbone_and_codec(tmp_path, monkeypatch):
    from vieneu._v3_turbo_engine.onnx_runtime_lite import OnnxV3LiteEngine

    original = OnnxV3LiteEngine._fetch
    monkeypatch.setattr(OnnxV3LiteEngine, '_fetch', staticmethod(original))
    source = tmp_path / 'source'
    source.write_bytes(b'graph')
    monkeypatch.setattr(files, '_cached_or_download', lambda *args: source)
    files.install_sdk_fetch_hook(tmp_path / 'app')
    backbone = OnnxV3LiteEngine._fetch('backbone', ['a.onnx'], 'onnx_update')
    codec = OnnxV3LiteEngine._fetch('codec', ['b.onnx'], None)
    assert backbone != codec
    assert (backbone / 'a.onnx').read_bytes() == (codec / 'b.onnx').read_bytes() == b'graph'
