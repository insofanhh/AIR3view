from types import SimpleNamespace
import pytest
from backend import render
from backend.media import Cancelled


@pytest.mark.parametrize('returncode,expected',[(0,True),(1,False)])
def test_auto_requires_working_hardware_encode(monkeypatch,returncode,expected):
    monkeypatch.setattr(render,'_NVENC',None)
    calls=[]
    def probe(args,**kwargs):
        calls.append(args)
        assert 'h264_nvenc' in args and '-frames:v' in args
        assert kwargs['timeout']==10
        return SimpleNamespace(returncode=returncode)
    monkeypatch.setattr(render.subprocess,'run',probe)
    assert render.nvenc_available() is expected
    assert render.nvenc_available() is expected
    assert len(calls)==1


def test_auto_falls_back_on_device_error_only(monkeypatch,tmp_path):
    monkeypatch.setattr(render,'_NVENC',True)
    calls=[]
    def run(args,**kwargs):
        calls.append(args)
        if len(calls)==1:raise RuntimeError('OpenEncodeSessionEx failed: out of memory')
    monkeypatch.setattr(render,'run',run)
    chosen=render.run_encoded(['ffmpeg','-i','source.mp4'],['out.tmp.mp4'],'auto',tmp_path,lambda:None)
    assert chosen=='cpu'
    assert 'h264_nvenc' in calls[0] and 'libx264' in calls[1]
    assert calls[0][-1]==calls[1][-1]=='out.tmp.mp4'


@pytest.mark.parametrize('error',[RuntimeError('Invalid input data'),Cancelled('Stopped')])
def test_data_error_and_cancel_never_retry(monkeypatch,tmp_path,error):
    monkeypatch.setattr(render,'_NVENC',True)
    calls=[]
    def run(*a,**kw):
        calls.append(1)
        raise error
    monkeypatch.setattr(render,'run',run)
    with pytest.raises(type(error),match=str(error)):
        render.run_encoded([],[],'auto',tmp_path,lambda:None)
    assert len(calls)==1


def test_forced_cpu_does_not_probe_gpu(monkeypatch,tmp_path):
    monkeypatch.setattr(render,'nvenc_available',lambda:pytest.fail('CPU should not probe GPU'))
    calls=[]
    monkeypatch.setattr(render,'run',lambda args,**kw:calls.append(args))
    assert render.run_encoded([],[],'cpu',tmp_path,lambda:None)=='cpu'
    assert 'libx264' in calls[0]


def test_forced_nvenc_does_not_silently_switch(monkeypatch,tmp_path):
    calls=[]
    def fail(*a,**kw):
        calls.append(1)
        raise RuntimeError('No NVENC capable devices found')
    monkeypatch.setattr(render,'run',fail)
    with pytest.raises(RuntimeError,match='No NVENC'):
        render.run_encoded([],[],'nvenc',tmp_path,lambda:None)
    assert len(calls)==1
