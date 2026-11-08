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


class JPEGDecoder:
    """411: decode one bounded JPEG straight into a caller-owned BGRX buffer.

    The output buffer (e.g. the HDMI presenter's MIT-SHM segment) is reused;
    nothing is allocated per frame. The JPEG must have exactly the output size.
    """
    def __init__(self, library, width, height, *, loader=C.CDLL):
        if not PurePosixPath(library).is_absolute() or '\0' in library:
            raise ValueError('native JPEG library must be an absolute POSIX path')
        if not (1 <= width <= 1920 and 1 <= height <= 1080):
            raise ValueError('invalid JPEG dimensions')
        self.width, self.height = width, height
        self.handle = None
        self.lib = loader(library)
        integer = C.POINTER(C.c_int)
        for name, args, result in [
            ('tjInitDecompress', [], C.c_void_p),
            ('tjDecompressHeader3', [C.c_void_p,C.c_char_p,C.c_ulong,integer,integer,integer,integer], C.c_int),
            ('tjDecompress2', [C.c_void_p,C.c_char_p,C.c_ulong,C.c_void_p,C.c_int,C.c_int,C.c_int,C.c_int,C.c_int], C.c_int),
            ('tjDestroy', [C.c_void_p], C.c_int),
        ]:
            fn = getattr(self.lib, name); fn.argtypes, fn.restype = args, result
        self.handle = self.lib.tjInitDecompress()
        if not self.handle: raise RuntimeError('JPEG decompressor initialization failed')

    def decode(self, jpeg, target, pitch):
        if not self.handle: raise RuntimeError('JPEG decoder is closed')
        if not isinstance(jpeg, (bytes, bytearray)) or not 0 < len(jpeg) <= MAX_JPEG:
            raise ValueError('JPEG frame outside bound')
        if not target or not self.width*4 <= pitch <= self.width*4+256:
            raise ValueError('invalid BGRX target or stride')
        data = bytes(jpeg)
        width, height, sampling, colorspace = C.c_int(), C.c_int(), C.c_int(), C.c_int()
        if self.lib.tjDecompressHeader3(self.handle, data, len(data), C.byref(width), C.byref(height),
                                        C.byref(sampling), C.byref(colorspace)):
            raise RuntimeError('JPEG header rejected')
        if (width.value, height.value) != (self.width, self.height):
            raise ValueError('JPEG size does not match the output')
        # TJPF_BGRX=3 matches the X11 TrueColor layout; flags 0.
        if self.lib.tjDecompress2(self.handle, data, len(data), target, self.width, pitch, self.height, 3, 0):
            raise RuntimeError('native JPEG decode failed')

    def close(self):
        if self.handle:
            self.lib.tjDestroy(self.handle); self.handle = None

    def __enter__(self): return self
    def __exit__(self, *_): self.close()
