#!/usr/bin/env python3
"""승인 후 사용하는 오프라인 호스트 도구. 기본은 계획 출력이며 --execute가 쓰기 경계다.

QCOW2 원본의 해시를 확인하고 새 ext4 템플릿 또는 새 인스턴스만 만든다.
기존 출력/인스턴스는 절대 갱신하지 않으며, SD/물리 블록 장치를 출력으로 받지 않는다.
"""
import argparse
import gzip
import hashlib
import importlib.util
import json
import lzma
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'platform/guests/common'))
sys.path.insert(0, str(ROOT / 'platform/first-install'))
sys.path.insert(0, str(ROOT / 'platform/yocto/meta-xenova/recipes-core/xenova-platform/files'))
from xenova_runtime import operation_lock
from prepare_rootfs import prepare_template, inside
import template_recipe
import guest_display_template
import guest_agent_template
from prepare_instance import Workspace, make_instance as shared_make_instance


def command(args, **kwargs):
    kwargs.setdefault('check', True)
    return subprocess.run([str(a) for a in args], **kwargs)


def digest(path, algorithm='sha256'):
    h = hashlib.new(algorithm)
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def require_new_output(path):
    if not path.is_absolute() or path.is_symlink() or path.exists():
        raise ValueError('output must be a new absolute path, never an existing image/instance')
    if path.name.startswith('.') or not path.parent.is_dir():
        raise ValueError('output needs an existing parent and a non-hidden name')
    if str(path.resolve()).startswith(('/dev/', '/proc/', '/sys/')):
        raise ValueError('output cannot be a device or virtual filesystem')


def kernel_config_ok(text):
    values = dict(line.split('=', 1) for line in text.splitlines() if line.startswith('CONFIG_') and '=' in line)
    for symbol in ('XEN', 'HVC_XEN', 'BLK_DEV_INITRD', 'DEVTMPFS'):
        if values.get('CONFIG_' + symbol) != 'y':
            raise ValueError(f'guest kernel requires built-in CONFIG_{symbol}')
    for symbol in ('XEN_BLKDEV_FRONTEND', 'XEN_NETDEV_FRONTEND', 'EXT4_FS'):
        if values.get('CONFIG_' + symbol) not in ('y', 'm'):
            raise ValueError(f'guest kernel missing CONFIG_{symbol}')
    return values


def raw_kernel(source, destination):
    data = source.read_bytes()
    if data.startswith(b'\x1f\x8b'):
        data = gzip.decompress(data)
    elif data.startswith(b'\xfd7zXZ\x00'):
        data = lzma.decompress(data)
    elif data.startswith(b'\x28\xb5\x2f\xfd'):
        data = subprocess.check_output(['zstd', '-dc', str(source)])
    if data[56:60] != b'ARM\x64':
        raise ValueError('kernel is not an uncompressed Arm64 Linux Image')
    destination.write_bytes(data)



def mount_source_boot(work, root, partitions):
    """원본 fstab의 별도 /boot만 같은 private loop 안에서 찾아 읽기 전용으로 연결한다."""
    fstab = inside(root, 'etc/fstab', create_parents=False)
    entries = [row for line in fstab.read_text().splitlines()
               if len(row := line.split('#', 1)[0].split()) >= 3 and row[1] == '/boot']
    if not entries:
        return
    if len(entries) != 1 or entries[0][2] != 'ext4':
        raise ValueError('unsupported or ambiguous separate guest /boot filesystem')
    source = entries[0][0]
    match = __import__('re').fullmatch(r'(LABEL|UUID|PARTUUID)=([A-Za-z0-9_.-]+)', source)
    if not match:
        raise ValueError('separate guest /boot must use LABEL/UUID/PARTUUID on the private source loop')
    key = {'LABEL': 'LABEL', 'UUID': 'UUID', 'PARTUUID': 'PART_ENTRY_UUID'}[match[1]]
    matches = []
    for partition in partitions:
        fields = subprocess.check_output(['blkid', '-p', '-o', 'export', str(partition)], text=True)
        info = dict(line.split('=', 1) for line in fields.splitlines() if '=' in line)
        if info.get('TYPE') == 'ext4' and info.get(key) == match[2]:
            matches.append(partition)
    if len(matches) != 1:
        raise ValueError('separate guest /boot is missing or duplicated on the private source loop')
    target = inside(root, 'boot', create_parents=False)
    if not target.is_dir():
        raise ValueError('source /boot mountpoint must already exist on the read-only root')
    work.mount(matches[0], target, readonly=True)


def root_partition(work, disk):
    loop = work.loop(disk, readonly=True, partitions=True)
    for _ in range(20):
        partitions = sorted(Path('/dev').glob(Path(loop).name + 'p[0-9]*'))
        if partitions:
            break
        time.sleep(0.25)
    for index, partition in enumerate(partitions):
        if subprocess.run(['blkid', '-o', 'value', '-s', 'TYPE', str(partition)], text=True, stdout=subprocess.PIPE, check=False).stdout.strip() != 'ext4':
            continue
        target = work.path / f'source-{index}'
        work.mount(partition, target, readonly=True)
        if inside(target, 'etc/os-release', create_parents=False).is_file():
            mount_source_boot(work, target, partitions)
            return target
    raise ValueError('no ext4 Linux root partition found on the private source loop')


def boot_assets(root, output, source):
    release = source['kernel_release']
    config = inside(root, 'boot/config-' + release, create_parents=False)
    kernel_config_ok(config.read_text())
    if not inside(root, 'lib/modules/' + release, create_parents=False).is_dir():
        raise ValueError('matching guest kernel modules absent')
    version = subprocess.check_output(['chroot', str(root), 'dpkg-query', '-W', '-f=${Version}', source['kernel_package']], text=True)
    if version != source['kernel_package_version']:
        raise ValueError('guest kernel package version differs from sources.lock.json')
    modules = inside(root, 'etc/initramfs-tools/modules')
    with modules.open('a') as f:
        f.write('\n# Xenova Xen root disk / network and ext4\nxen-blkfront\nxen-netfront\next4\n')
    # 네이티브 ARM64 또는 등록된 binfmt가 필요하다. apt 설치는 앞선 Agent 준비 단계만 수행한다.
    command(['chroot', root, '/bin/true'])
    command(['chroot', root, 'update-initramfs', '-u', '-k', release])
    initrd = inside(root, 'boot/initrd.img-' + release, create_parents=False)
    entries = subprocess.check_output(['chroot', str(root), 'lsinitramfs', '/boot/' + initrd.name], text=True)
    values = kernel_config_ok(config.read_text())
    for symbol, filename in [('XEN_BLKDEV_FRONTEND', 'xen-blkfront.ko'), ('XEN_NETDEV_FRONTEND', 'xen-netfront.ko'), ('EXT4_FS', 'ext4.ko')]:
        if values['CONFIG_' + symbol] == 'm' and filename not in entries:
            raise ValueError(f'{filename} absent from generated initramfs')
    raw_kernel(inside(root, 'boot/vmlinuz-' + release, create_parents=False), output / 'Image')
    shutil.copyfile(initrd, output / 'initrd')
    shutil.copyfile(config, output / 'kernel.config')
    command(['chroot', root, 'apt-mark', 'manual', source['kernel_package']])
    command(['chroot', root, 'apt-mark', 'hold', source['kernel_package'], source['kernel_metapackage']])
    (output / 'boot.json').write_text(json.dumps({
        'schema': 1, 'kernel_release': release, 'root_device': '/dev/xvda',
        'kernel_sha256': digest(output / 'Image'), 'initrd_sha256': digest(output / 'initrd'),
        'config_sha256': digest(output / 'kernel.config'), 'source': source,
    }, indent=2) + '\n')


def make_template(args, source):
    if not args.input.is_file() or digest(args.input, source['hash_algorithm']) != source['digest']:
        raise ValueError('official input digest mismatch or input is not a regular file')
    recipe = getattr(args, 'recipe', None) or ROOT / 'platform/guests/linux' / args.source / 'recipe.json'
    preparation = template_recipe.source_info(recipe, args.source)
    work = Workspace(args.output.parent)
    try:
        disk = work.path / 'source.raw'
        command(['qemu-img', 'convert', '-f', source['format'], '-O', 'raw', args.input, disk])
        original = root_partition(work, disk)
        expected_id = 'ID=' + source['distribution']
        os_release = inside(original, 'etc/os-release', create_parents=False).read_text().replace('"', '')
        if expected_id not in os_release.splitlines():
            raise ValueError('upstream distribution differs from source lock')
        stage = work.path / 'template'
        stage.mkdir()
        raw = stage / 'root.raw'
        with raw.open('xb') as f:
            f.truncate(args.size_gib * 1024 ** 3)
        command(['mkfs.ext4', '-F', '-L', 'xenova-root', raw])
        root = work.path / 'root'
        work.mount(work.loop(raw), root)
        command(['cp', '-a', str(original) + '/.', root])
        template_recipe.apply(root, recipe, args.source)
        prepare_template(root)
        display_guest = None
        if 'guest_display' in preparation['recipe']:
            display_guest = guest_display_template.prepare(
                root, preparation['recipe']['guest_display'],
                web_assets=getattr(args, 'display_web_assets', None),
                web_revision=getattr(args, 'display_web_revision', None))
        # chroot 내 /dev, /proc, /sys는 작업 루트에만 마운트한다.
        for fs, relative in [('proc', 'proc'), ('sysfs', 'sys'), ('devtmpfs', 'dev')]:
            target = inside(root, relative)
            target.mkdir(exist_ok=True)
            command(['mount', '-t', fs, fs, target])
            work.mounts.append(target)
        agent = None
        if 'guest_agent' in preparation['recipe']:
            agent = guest_agent_template.prepare(root, preparation['recipe']['guest_agent'])
        boot_assets(root, stage, source)
        os.sync()
        work.close()
        work.mounts, work.loops = [], []
        metadata = {'schema': 1, 'id': args.source, 'source': source,
                    'size_gib': args.size_gib, 'root_sha256': digest(raw),
                    'schema_version': '1.0', 'template_id': {'ubuntu-24.04-20260926':'ubuntu-24.04-arm64','debian-13-20261015':'debian-13-arm64'}[args.source],
                    'version': preparation['recipe']['version'], 'platform': 'xenova',
                    'preparation': preparation, 'guest_agent': agent, 'guest_display': display_guest,
                    'files': {name: {'sha256': digest(stage / name), 'bytes': (stage / name).stat().st_size}
                              for name in ('root.raw', 'Image', 'initrd', 'kernel.config', 'boot.json')}}
        from xenova_guest_profile import from_manifest
        metadata['provisioning_profile']=from_manifest(metadata)
        if template_recipe.source_info(recipe, args.source) != preparation:
            raise ValueError('template source changed during preparation; output not published')
        (stage / 'template.json').write_text(json.dumps(metadata, indent=2) + '\n')
        for path in stage.iterdir():
            path.chmod(0o444)
        require_new_output(args.output)
        stage.rename(args.output)
    finally:
        if work.mounts or work.loops:
            work.close()


def make_instance(args):
    spec = importlib.util.spec_from_file_location('xenova_vm', ROOT / 'platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_vm.py')
    vm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vm)
    return shared_make_instance(args, vm=vm, defaults_path=ROOT / 'platform/yocto/meta-xenova/recipes-core/xenova-platform/files/defaults.json')


def initialize_storage(data, *, seed=False):
    # 포맷·마운트하지 않고 사전에 마운트한 빈 전용 영역만 등록한다.
    result = json.loads(subprocess.check_output(['findmnt', '--json', '--mountpoint', str(data), '-o', 'TARGET,SOURCE,FSTYPE,OPTIONS'], text=True))['filesystems'][0]
    if result['fstype'] != 'ext4' or result['target'] != str(data) or not result['source'].startswith('/dev/') or 'rw' not in result['options'].split(','):
        raise ValueError('dedicated writable ext4 mount required')
    label = subprocess.check_output(['blkid', '-o', 'value', '-s', 'LABEL', result['source']], text=True).strip()
    if label != 'xenova-data':
        raise ValueError('dedicated filesystem must already have label xenova-data')
    if any(p.name != 'lost+found' for p in data.iterdir()):
        raise ValueError('storage is not empty; no existing data is adopted/overwritten')
    for name in ('templates', 'instances', *(['seeds'] if seed else [])):
        (data / name).mkdir(mode=0o700)
    if seed:
        (data / '.xenova-seed-store.json').write_text(json.dumps({'schema': 1, 'purpose': 'factory-seeds'}) + '\n')
    else:
        (data / '.xenova-storage.json').write_text(json.dumps({'schema': 1, 'uuid': str(uuid.uuid4())}) + '\n')
    os.sync()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='explicitly permit creation in new output paths')
    sub = parser.add_subparsers(dest='action', required=True)
    template = sub.add_parser('template')
    template.add_argument('--source', required=True)
    template.add_argument('--input', type=Path, required=True)
    template.add_argument('--output', type=Path, required=True)
    template.add_argument('--size-gib', type=int, default=8)
    template.add_argument('--recipe', type=Path, help='editable recipe.json; defaults to the selected Linux profile')
    template.add_argument('--display-web-assets', type=Path,
                          help='canonical vm-screens dist directory required by display guest recipes')
    template.add_argument('--display-web-revision',
                          help='7-40 lowercase hex frontend commit recorded with display web assets')
    instance = sub.add_parser('instance')
    instance.add_argument('--data-root', type=Path, required=True)
    instance.add_argument('--id', required=True)
    instance.add_argument('--spec', type=Path)
    instance.add_argument('--template-root', type=Path, help='verified external template bundle, factory seed mode only')
    instance.add_argument('--deployment-seed', action='store_true', help='new factory seeds only; defer guest identity until first boot')
    storage = sub.add_parser('storage-init')
    storage.add_argument('--data-root', type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.execute:
        print('PLAN ONLY: no image, mount, initialization or download performed.', vars(args))
        return
    if os.geteuid() != 0:
        raise ValueError('--execute requires root on an approved Linux preparation host')
    tools = ['findmnt', 'blkid'] if args.action == 'storage-init' else ['losetup', 'mount', 'umount']
    if args.action == 'template':
        tools += ['qemu-img', 'mkfs.ext4', 'chroot', 'cp', 'blkid']
    elif args.action == 'instance':
        tools += ['ssh-keygen', 'tune2fs', 'chroot']
    for tool in tools:
        if not shutil.which(tool):
            raise ValueError(f'required preparation tool unavailable: {tool}; install only after separate approval')
    if args.action == 'template':
        require_new_output(args.output)
        if not 4 <= args.size_gib <= 32:
            raise ValueError('template size must be 4..32 GiB')
        source = json.loads((ROOT / 'platform/guests/sources.lock.json').read_text())[args.source]
        make_template(args, source)
    else:
        if not args.data_root.is_absolute() or args.data_root.is_symlink():
            raise ValueError('data-root must be an absolute mountpoint without symlink')
        # 모든 게시·최초 생성은 같은 호스트에서 직렬화한다. 보드가 꺼진 오프라인 작업이다.
        with operation_lock():
            if args.action == 'storage-init':
                initialize_storage(args.data_root)
            else:
                make_instance(args)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, StopIteration, subprocess.CalledProcessError) as exc:
        print('[xenova-prepare] ERROR:', exc, file=sys.stderr)
        sys.exit(1)
