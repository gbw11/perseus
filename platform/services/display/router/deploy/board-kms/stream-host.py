"""MJPEG sender using existing state/graphics and isolated unprivileged workers."""
import argparse
import os
from pathlib import Path, PurePosixPath
import subprocess

from xenova_display import stream
from xenova_display.outputs import RuntimeConfig


def prepare():
    root = Path(os.environ['XENOVA_GRAPHICS_ROOT']).resolve(strict=True)
    base = RuntimeConfig.load(root/'run/xenova-display-runtime.json').model_dump()
    base.update(encoder=os.environ.get('XENOVA_STREAM_ENCODER','ffmpeg'),
                native_jpeg_library=os.environ.get('XENOVA_NATIVE_JPEG_LIBRARY'),
                native_jpeg_quality=int(os.environ.get('XENOVA_NATIVE_JPEG_QUALITY','85')))
    if 'XENOVA_STREAM_FPS' in os.environ:
        base['fps'] = int(os.environ['XENOVA_STREAM_FPS'])
    config = RuntimeConfig.model_validate(base)
    worker_lib = os.environ['XENOVA_STREAM_WORKER_LIB']
    if not PurePosixPath(worker_lib).is_absolute() or '\0' in worker_lib:
        raise ValueError('worker package path must be absolute')
    package = (root/worker_lib.lstrip('/')).resolve(strict=True)
    if not package.is_relative_to(root) or not (package/'xenova_display/native_capture.py').is_file():
        raise ValueError('worker package is outside the graphics root or incomplete')
    if config.encoder == 'native_jpeg':
        library = (root/config.native_jpeg_library.lstrip('/')).resolve(strict=True)
        if not library.is_relative_to(root): raise ValueError('library escaped graphics root')
    owners = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit(): continue
        try:
            if (proc/'comm').read_text().strip() == 'Xorg' and (proc/'root').samefile(root):
                owners.append(proc)
        except (OSError, ValueError): pass
    if len(owners) != 1: raise RuntimeError('expected one owned graphics-root Xorg')
    namespace = owners[0]/'ns/mnt'
    if os.readlink(namespace) == os.readlink('/proc/self/ns/mnt'):
        raise RuntimeError('expected the dedicated graphics mount namespace')
    lib = root/'usr/lib/aarch64-linux-gnu'
    original = stream.capture_command
    def command(cfg, output):
        argv = original(cfg, output)
        executable = '/usr/bin/python3' if cfg.encoder == 'native_jpeg' else '/usr/bin/ffmpeg'
        # Workers receive X11 access and package paths only, never HTTP/source keys.
        return [str(lib/'ld-linux-aarch64.so.1'),'--library-path',str(lib),
            str(root/'usr/bin/nsenter'),'--mount='+str(namespace),'chroot',str(root),
            '/usr/bin/setpriv','--reuid=1000','--regid=1000','--clear-groups',
            '/usr/bin/env','-i','PATH=/usr/bin:/bin','HOME=/home/kiosk',
            'DISPLAY='+cfg.display,'XAUTHORITY=/run/xenova-display.Xauthority',
            'PYTHONPATH='+worker_lib,executable,*argv[1:]]
    stream.capture_command = command
    return config, root/'run/xenova-kiosk/display-runtime.json'


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--check',action='store_true')
    p.add_argument('--port',type=int,default=8083)
    args = p.parse_args(); config,status = prepare()
    if args.check:
        for output in config.outputs:
            command = stream.capture_command(config,output)
            if config.encoder == 'native_jpeg':
                command.append('--check')
            else:
                # Validate the fallback executable without starting a capture.
                executable = command.index('/usr/bin/ffmpeg')
                command = [*command[:executable+1], '-version']
            subprocess.run(command,check=True,timeout=15)
    else:
        app = stream.create_stream_app(config,status)
        stream.StreamServer(stream.uvicorn.Config(app,host='127.0.0.1',port=args.port,
            access_log=False,timeout_graceful_shutdown=5)).run()
