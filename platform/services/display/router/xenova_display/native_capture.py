"""Continuous X11/MIT-SHM -> reusable TurboJPEG -> bounded JPEG stdout worker."""
import argparse
import signal
import sys
import time

from .native_jpeg import JPEGEncoder
from .native_x11 import SharedCapture


class StopRequested(BaseException): pass


def run(args, *, source_factory=SharedCapture, encoder_factory=JPEGEncoder,
        sink=None, clock=time.monotonic, sleep=time.sleep):
    sink = sink if sink is not None else sys.stdout.buffer
    with source_factory(args.width, args.height, args.x, args.y, display=args.display) as source:
        with encoder_factory(args.library, args.width, args.height, args.quality) as encoder:
            if args.check: return
            interval, deadline, produced = 1/args.fps, clock(), 0
            while args.frames is None or produced < args.frames:
                remaining = deadline-clock()
                if remaining > 0: sleep(remaining)
                jpeg = encoder.encode(source.frame(), source.pitch)
                sink.write(jpeg); sink.flush()
                produced += 1
                # A late consumer receives current pixels; do not produce bursts
                # to replay missed deadlines. Keep no frame/statistics history.
                deadline = max(deadline+interval, clock())


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--library', required=True)
    p.add_argument('--display', default=':0')
    for name, default in [('width',1280),('height',720),('x',0),('y',0),('fps',5),('quality',85)]:
        p.add_argument('--'+name, type=int, default=default)
    p.add_argument('--frames', type=int, help='Optional bounded diagnostic; omitted for continuous service')
    p.add_argument('--check', action='store_true', help='Validate native dependencies and release resources')
    return p


def main():
    p = parser(); args = p.parse_args()
    if not (1 <= args.fps <= 15 and (args.frames is None or args.frames > 0)):
        p.error('invalid frame rate or frame count')
    def stop(*_): raise StopRequested()
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    try:
        run(args)
    except (StopRequested, BrokenPipeError): return 0
    except (OSError, RuntimeError, ValueError, MemoryError) as exc:
        print('native_capture_unavailable: '+type(exc).__name__+': '+str(exc),file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__': raise SystemExit(main())
