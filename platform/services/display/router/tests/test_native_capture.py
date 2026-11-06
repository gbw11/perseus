import asyncio
import ctypes as C
import io
from types import SimpleNamespace

import pytest
from xenova_display.native_capture import run
from xenova_display.native_jpeg import JPEGEncoder
from xenova_display.native_x11 import SharedCapture
from xenova_display.outputs import RuntimeConfig
from xenova_display.stream import capture_command
from test_outputs import config


def test_native_backend_is_explicit_and_requires_library_and_matching_geometry():
    c = config()
    assert c.encoder == 'ffmpeg' and capture_command(c,c.outputs[0])[0] == 'ffmpeg'
    raw = c.model_dump(); raw['encoder'] = 'native_jpeg'
    with pytest.raises(ValueError): RuntimeConfig.model_validate(raw)
    raw['native_jpeg_library'] = 'relative.so'
    with pytest.raises(ValueError): RuntimeConfig.model_validate(raw)
    raw['native_jpeg_library'] = '/opt/native/libturbojpeg.so'
    native = RuntimeConfig.model_validate(raw)
    cmd = capture_command(native,native.outputs[1])
    assert 'xenova_display.native_capture' in cmd and cmd[cmd.index('--x')+1] == '1280'
    assert '--frames' not in cmd and cmd[cmd.index('--quality')+1] == '85'
    raw['stream_width'] = 640
    with pytest.raises(ValueError): RuntimeConfig.model_validate(raw)


@pytest.mark.parametrize('rectangle',[(-1,720,0,0),(1280,720,-1,0),(1921,720,0,0),(1280,1081,0,0)])
def test_invalid_geometry_is_rejected_before_loading_native_libraries(rectangle,monkeypatch):
    monkeypatch.setattr(C,'CDLL',lambda *_args,**_kwargs:pytest.fail('should not load native code'))
    with pytest.raises(ValueError): SharedCapture(*rectangle)


def test_worker_closes_both_resources_after_broken_receiver():
    closed=[]
    class Source:
        pitch=5120
        def __init__(self,*_args,**_kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*_): closed.append('source')
        def frame(self): return 123
    class Encoder:
        def __init__(self,*_): pass
        def __enter__(self): return self
        def __exit__(self,*_): closed.append('encoder')
        def encode(self,*_): return b'jpeg'
    class Broken:
        def write(self,_): raise BrokenPipeError()
    args=SimpleNamespace(width=1280,height=720,x=0,y=0,display=':5',library='/unused',quality=85,fps=5,frames=None,check=False)
    with pytest.raises(BrokenPipeError): run(args,source_factory=Source,encoder_factory=Encoder,sink=Broken())
    assert closed == ['encoder','source']


class Function:
    def __init__(self,fn): self.fn=fn
    def __call__(self,*args): return self.fn(*args)


def fake_jpeg(*,capacity=128,compress_size=4):
    buffer=C.create_string_buffer(b'jpeg',capacity); freed=[]
    def compress(_h,_pixels,_w,_pitch,_height,_pf,target,size,_sampling,_quality,flags):
        assert flags==1024
        C.cast(size,C.POINTER(C.c_ulong)).contents.value=compress_size
        return 0
    lib=SimpleNamespace(tjInitCompress=Function(lambda:1),tjBufSize=Function(lambda *_:capacity),
        tjAlloc=Function(lambda _:C.addressof(buffer)),tjCompress2=Function(compress),
        tjFree=Function(lambda ptr:freed.append(('buffer',ptr))),tjDestroy=Function(lambda ptr:freed.append(('handle',ptr))))
    return lib,buffer,freed


def test_encoder_reuses_one_buffer_and_closes_idempotently():
    lib,keep,freed=fake_jpeg()
    encoder=JPEGEncoder('/unused',1280,720,loader=lambda _:lib)
    assert encoder.encode(1,5120)==b'jpeg' and encoder.encode(1,5120)==b'jpeg'
    encoder.close(); encoder.close()
    assert [kind for kind,_ in freed] == ['buffer','handle']


def test_oversized_jpeg_is_rejected_before_reading_output_buffer():
    lib,keep,freed=fake_jpeg(compress_size=2*1024*1024+1)
    with JPEGEncoder('/unused',1280,720,loader=lambda _:lib) as encoder:
        with pytest.raises(RuntimeError,match='frame bound'): encoder.encode(1,5120)
    assert [kind for kind,_ in freed] == ['buffer','handle']


def test_failed_native_allocation_releases_compressor():
    lib,keep,freed=fake_jpeg();lib.tjAlloc=Function(lambda _:None)
    with pytest.raises(MemoryError): JPEGEncoder('/unused',1280,720,loader=lambda _:lib)
    assert freed == [('handle',1)]
