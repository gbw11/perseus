"""Bounded reusable TurboJPEG buffer; this module has no HTTP dependencies.

ABI: https://github.com/libjpeg-turbo/libjpeg-turbo/blob/2.1.5.1/turbojpeg.h
"""
import ctypes as C
from pathlib import PurePosixPath
from .capture_limits import MAX_JPEG, MAX_NATIVE_ALLOCATION


class JPEGEncoder:
    def __init__(self, library, width, height, quality=85, *, loader=C.CDLL):
        if not PurePosixPath(library).is_absolute() or '\0' in library:
            raise ValueError('native JPEG library must be an absolute POSIX path')
        if not (1 <= width <= 1920 and 1 <= height <= 1080 and 1 <= quality <= 100):
            raise ValueError('invalid JPEG dimensions or quality')
        self.width, self.height, self.quality = width, height, quality
        self.handle, self.buffer = None, None
        self.lib = loader(library)
        for name, args, result in [
            ('tjInitCompress', [], C.c_void_p),
            ('tjBufSize', [C.c_int,C.c_int,C.c_int], C.c_ulong),
            ('tjAlloc', [C.c_int], C.c_void_p),
            ('tjCompress2', [C.c_void_p,C.c_void_p,C.c_int,C.c_int,C.c_int,C.c_int,C.POINTER(C.c_void_p),C.POINTER(C.c_ulong),C.c_int,C.c_int,C.c_int], C.c_int),
            ('tjFree', [C.c_void_p], None),
            ('tjDestroy', [C.c_void_p], C.c_int),
        ]:
            fn = getattr(self.lib, name); fn.argtypes, fn.restype = args, result
        try:
            self.handle = self.lib.tjInitCompress()
            if not self.handle: raise RuntimeError('JPEG compressor initialization failed')
            self.capacity = self.lib.tjBufSize(width, height, 2)  # TJSAMP_420
            if not 0 < self.capacity <= MAX_NATIVE_ALLOCATION:
                raise RuntimeError('JPEG buffer estimate exceeds allocation bound')
            self.buffer = self.lib.tjAlloc(self.capacity)
            if not self.buffer: raise MemoryError('JPEG buffer allocation failed')
        except BaseException:
            self.close(); raise

    def encode(self, pixels, pitch):
        if not self.handle or not self.buffer: raise RuntimeError('JPEG encoder is closed')
        if not pixels or not self.width*4 <= pitch <= self.width*4+256:
            raise ValueError('invalid BGRX buffer or stride')
        target, size = C.c_void_p(self.buffer), C.c_ulong(self.capacity)
        # TJPF_BGRX=3, TJSAMP_420=2, TJFLAG_NOREALLOC=1024. Reuse the
        # library-sized allocation; never allocate/free a JPEG buffer per frame.
        rc = self.lib.tjCompress2(self.handle, pixels, self.width, pitch, self.height,
                                  3, C.byref(target), C.byref(size), 2, self.quality, 1024)
        if rc: raise RuntimeError('native JPEG compression failed')
        if target.value != self.buffer: raise RuntimeError('JPEG buffer unexpectedly changed')
        if not 0 < size.value <= min(self.capacity, MAX_JPEG):
            raise RuntimeError('encoded JPEG exceeds frame bound')
        return C.string_at(self.buffer, size.value)

    def close(self):
        if self.buffer:
            self.lib.tjFree(self.buffer); self.buffer = None
        if self.handle:
            self.lib.tjDestroy(self.handle); self.handle = None

    def __enter__(self): return self
    def __exit__(self, *_): self.close()
