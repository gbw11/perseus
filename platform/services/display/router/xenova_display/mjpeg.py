"""Stdlib-only MJPEG helpers shared by dom0 and the display domU worker.

No HTTP, GUI or third-party imports: the display domU (410 Plan B) runs
`guest_sender` with the Python standard library only.
"""
from .capture_limits import MAX_JPEG


class JpegParser:
    """Split a concatenated MJPEG byte stream into bounded JPEG frames."""
    def __init__(self):
        self.buffer = bytearray()
        self.discarded = 0  # frames dropped because they exceeded MAX_JPEG

    def push(self, data):
        self.buffer.extend(data)
        frames = []
        while True:
            start = self.buffer.find(b"\xff\xd8")
            if start == -1:
                self.buffer = self.buffer[-1:]
                break
            if start: del self.buffer[:start]
            end = self.buffer.find(b"\xff\xd9", 2)
            if end == -1:
                if len(self.buffer) > MAX_JPEG:
                    self.buffer.clear()
                    self.discarded += 1
                break
            if end+2 <= MAX_JPEG: frames.append(bytes(self.buffer[:end+2]))
            else: self.discarded += 1
            del self.buffer[:end+2]
        return frames


# Start-of-frame markers that carry the image size (baseline, progressive, ...).
_SOF = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})


def jpeg_dimensions(frame):
    """Return (width, height) from the first SOF segment, or None.

    Bounded marker walk over untrusted bytes: never reads past the buffer,
    never follows more than 64 segments and rejects SOS/EOI before a SOF.
    It does not decode pixels and is not a full JPEG validator.
    """
    n = len(frame)
    if n < 4 or frame[:2] != b"\xff\xd8" or frame[-2:] != b"\xff\xd9":
        return None
    i = 2
    for _ in range(64):
        if i >= n or frame[i] != 0xFF:
            return None
        while i < n and frame[i] == 0xFF:  # fill bytes
            i += 1
        if i >= n:
            return None
        marker = frame[i]
        i += 1
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:  # standalone markers
            continue
        if marker in (0xD8, 0xD9, 0xDA):
            return None
        if i+2 > n:
            return None
        length = int.from_bytes(frame[i:i+2], "big")
        if length < 2 or i+length > n:
            return None
        if marker in _SOF:
            if length < 7:
                return None
            height = int.from_bytes(frame[i+3:i+5], "big")
            width = int.from_bytes(frame[i+5:i+7], "big")
            return (width, height) if width and height else None
        i += length
    return None
