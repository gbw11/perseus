"""Structurally valid JPEG byte strings for relay tests (header only, not decodable pixels)."""


def fake_jpeg(width=1280, height=720, pad=0, tag=b""):
    sof = (b"\xff\xc0\x00\x11\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big")
           + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01")
    comments = b""
    data = tag + b"x" * pad
    while data:
        chunk, data = data[:60000], data[60000:]
        comments += b"\xff\xfe" + (len(chunk)+2).to_bytes(2, "big") + chunk
    return b"\xff\xd8" + sof + comments + b"\xff\xd9"
