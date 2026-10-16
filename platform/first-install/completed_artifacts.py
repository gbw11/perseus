"""완료 플랫폼의 독립 사본 게시와 읽기 전용 입력 검증. BitBake/SD/게스트는 실행하지 않는다."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time

from artifact_io import checked, new_path, publish, regular, sha

MANIFEST = 'platform-release.json'
RECORD = 'BUILD-RESULT.json'
KIND = 'perseus-completed-platform'


def build_record(path, expected):
    path = checked(path, expected)
    record = json.loads(path.read_text())
    if (record.get('build_exit_code') != 0 or not record.get('finished_at')
            or not re.fullmatch('[0-9a-f]{40}', record.get('source_commit', ''))):
        raise ValueError('completed successful platform build record required')
    build = Path(record['build_directory'])
    if not build.is_absolute() or os.path.normpath(str(build)) != str(build) or str(build).startswith(('/dev/', '/proc/', '/sys/')):
        raise ValueError('invalid build directory')
    return path, record, build


def image_entry(record, kind):
    if kind not in ('wic', 'rpi_sdimg'):
        raise ValueError('unsupported platform image kind')
    entry = record[kind]
    image = Path(entry['path'])
    suffixes = ('.wic', '.wic.bz2') if kind == 'wic' else ('.rpi-sdimg',)
    if (not image.is_absolute() or os.path.normpath(str(image)) != str(image) or not image.name.endswith(suffixes)
            or not re.fullmatch(r'[A-Za-z0-9_.+-]+', image.name)
            or type(entry.get('bytes')) is not int or entry['bytes'] <= 0
            or not re.fullmatch('[0-9a-f]{64}', entry.get('sha256', ''))):
        raise ValueError('invalid explicit platform image entry')
    return image, entry


def same_inode(left, right):
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def signature(path):
    value = path.stat()
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


@contextmanager
def publication_lock(build):
    """명시적 게시 중만 BitBake 호환 exclusive flock을 잡는다. 직접 만든 inode만 정리한다."""
    if not build.is_dir() or build.resolve() != build:
        raise ValueError('source build directory missing for publication')
    path = build / 'bitbake.lock'
    for _ in range(5):
        created = False
        flags = os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
        try:
            fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
        except FileExistsError:
            try:
                fd = os.open(path, flags)
            except FileNotFoundError:
                continue
        locked = False
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('BitBake lock is not a regular file')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError('platform build is still running; publication refused') from error
            locked = True
            try:
                current = path.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(current.st_mode) or not same_inode(info, current):
                continue  # BitBake 종료의 unlink/재생성과 경합했으므로 바뀐 inode를 다시 잠근다.
            yield
            return
        finally:
            if locked and created:
                try:
                    current = path.lstat()
                    if stat.S_ISREG(current.st_mode) and same_inode(os.fstat(fd), current):
                        path.unlink()
                except FileNotFoundError:
                    pass
            if locked:
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
    raise ValueError('BitBake lock changed repeatedly; retry publication after build shutdown')


def publication_plan(record_path, record_sha256, kind, output):
    """원본 이미지 bytes나 잠금은 열지 않는다. 완료 기록과 새 출력 경로만 검사한다."""
    record_path, record, build = build_record(record_path, record_sha256)
    image, entry = image_entry(record, kind)
    if not image.is_relative_to(build / 'tmp/deploy/images'):
        raise ValueError('source image must belong to the recorded build deploy directory')
    new_path(output)
    if output.is_relative_to(build):
        raise ValueError('completed artifact directory must be outside the build directory')
    return {'action': 'plan-publication', 'build_record': str(record_path), 'build_record_sha256': record_sha256,
            'image_kind': kind, 'source_image': entry, 'output_directory': str(output),
            'copy_bytes': entry['bytes'], 'source_image_verified': False,
            'execution_gate': 'BitBake-compatible exclusive lock, source hash, independent copy and hash verification',
            'source_commit': record['source_commit']}


def write_file(path, content):
    with path.open('xb') as out:
        out.write(content)
        out.flush()
        os.fsync(out.fileno())
    path.chmod(0o444)


def publish_completed(record_path, record_sha256, kind, output):
    """성공 기록을 보존하며 독립 일반 파일과 manifest를 새 디렉터리에 게시한다."""
    planned = publication_plan(record_path, record_sha256, kind, output)
    record_path, record, build = build_record(record_path, record_sha256)
    image, entry = image_entry(record, kind)
    with publication_lock(build):
        checked(record_path, record_sha256)
        checked(image, entry['sha256'])
        if image.stat().st_size != entry['bytes']:
            raise ValueError('source image size differs from completed build record')
        before = signature(image)
        stage = Path(tempfile.mkdtemp(prefix='.perseus-publishing-', dir=output.parent))
        copied = stage / image.name
        try:
            with image.open('rb') as inp, copied.open('xb') as out:
                for chunk in iter(lambda: inp.read(8 * 1024 * 1024), b''):
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            if signature(image) != before:
                raise ValueError('source image changed during publication')
            checked(image, entry['sha256'])
            checked(copied, entry['sha256'])
            if same_inode(image.stat(), copied.stat()) or copied.stat().st_size != entry['bytes']:
                raise ValueError('published image must be an independent complete copy')
            copied.chmod(0o444)
            checked(record_path, record_sha256)
            write_file(stage / RECORD, record_path.read_bytes())
            checked(stage / RECORD, record_sha256)
            manifest = {'schema': 1, 'kind': KIND, 'complete': True, 'release_name': output.name,
                        'published_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                        'source_build_directory': str(build), 'source_image_kind': kind,
                        'source_image': entry, 'source_commit': record['source_commit'],
                        'image': {'name': image.name, 'bytes': entry['bytes'], 'sha256': entry['sha256']},
                        'build_record': {'name': RECORD, 'sha256': record_sha256}}
            write_file(stage / MANIFEST, (json.dumps(manifest, indent=2) + '\n').encode())
            write_file(stage / 'SHA256SUMS', ''.join(f'{sha(stage / name)}  {name}\n' for name in (image.name, RECORD, MANIFEST)).encode())
            stage.chmod(0o555)
            fd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            publish(stage, output)
            return {'action': 'published', **platform_config(output)}
        except BaseException:
            print('Incomplete publication retained, never used as a release:', stage)
            raise


def platform_config(directory):
    manifest = json.loads((directory / MANIFEST).read_text())
    return {'platform': {'image': str(directory / manifest['image']['name']), 'sha256': manifest['image']['sha256'],
                         'build_record': str(directory / RECORD), 'build_record_sha256': manifest['build_record']['sha256'],
                         'release_manifest': str(directory / MANIFEST), 'release_manifest_sha256': sha(directory / MANIFEST)}}


@contextmanager
def completed_input(config):
    """게시 완료 사본만 읽는다. 원래 빌드 경로/bitbake.lock에는 접근하거나 쓰지 않는다."""
    platform = config['platform']
    if not platform.get('release_manifest') or not platform.get('release_manifest_sha256'):
        raise ValueError('published completed platform required; use publish-perseus-platform.py before assembly')
    manifest_path = checked(platform['release_manifest'], platform['release_manifest_sha256'])
    directory = manifest_path.parent
    if directory.is_symlink() or directory.resolve() != directory or directory.stat().st_mode & 0o222:
        raise ValueError('published platform directory must be canonical and read-only')
    manifest = json.loads(manifest_path.read_text())
    if manifest_path.name != MANIFEST or manifest.get('schema') != 1 or manifest.get('kind') != KIND or manifest.get('complete') is not True or manifest.get('release_name') != directory.name or directory.name.startswith('.'):
        raise ValueError('incomplete platform publication manifest')
    record_path, record, build = build_record(platform['build_record'], platform['build_record_sha256'])
    origin, entry = image_entry(record, manifest['source_image_kind'])
    if (manifest['source_image'] != entry or manifest['source_commit'] != record['source_commit']
            or manifest['source_build_directory'] != str(build)
            or manifest['build_record'] != {'name': RECORD, 'sha256': platform['build_record_sha256']}):
        raise ValueError('publication provenance differs from completed build record')
    expected_image = {'name': origin.name, 'bytes': entry['bytes'], 'sha256': entry['sha256']}
    if manifest['image'] != expected_image:
        raise ValueError('publication image differs from original artifact')
    image = Path(platform['image'])
    if record_path != directory / RECORD or image != directory / origin.name or directory.is_relative_to(build):
        raise ValueError('assembler requires an independent published artifact outside the build directory')
    if platform['sha256'] != entry['sha256']:
        raise ValueError('publication image hash differs from configuration')
    paths = (manifest_path, record_path, image, directory / 'SHA256SUMS')
    for path in paths:
        regular(path)
        if path.stat().st_mode & 0o222 or path.stat().st_nlink != 1:
            raise ValueError('published assets must be read-only independent regular files')
    if {p.name for p in directory.iterdir()} != {p.name for p in paths}:
        raise ValueError('unexpected file in published platform directory')
    checked(image, entry['sha256'])
    if image.stat().st_size != entry['bytes']:
        raise ValueError('published image size differs from completed record')
    checksums = (directory / 'SHA256SUMS').read_text().splitlines()
    if checksums != [f'{sha(p)}  {p.name}' for p in (image, record_path, manifest_path)]:
        raise ValueError('published checksum list mismatch')
    before = {p: signature(p) for p in paths}
    try:
        yield image, record
    finally:
        if any(signature(p) != value for p, value in before.items()):
            raise ValueError('published input changed while being used')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-record', type=Path, required=True)
    parser.add_argument('--build-record-sha256', required=True)
    parser.add_argument('--image-kind', choices=('wic', 'rpi_sdimg'), default='wic')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--execute', action='store_true', help='copy existing completed artifact only; never build or assemble')
    args = parser.parse_args(argv)
    if args.execute:
        result = publish_completed(args.build_record, args.build_record_sha256, args.image_kind, args.output_dir)
    else:
        result = publication_plan(args.build_record, args.build_record_sha256, args.image_kind, args.output_dir)
    print(json.dumps(result, indent=2))
