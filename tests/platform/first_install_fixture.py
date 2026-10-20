"""수백 바이트의 가짜 블록 장치·sysfs fixture. 실제 이미지/loop/장치/네트워크를 사용하지 않는다."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct
import sys

ROOT = Path(__file__).resolve().parents[2]
FILES = ROOT / 'platform/yocto/meta-xenova/recipes-core/xenova-platform/files'
sys.path.insert(0, str(ROOT / 'platform/first-install'))
sys.path.insert(0, str(FILES))
sys.path.insert(0, str(ROOT / 'platform/guests/common'))
import assemble
import first_install_contract as contract
import xenova_first_boot as boot
import xenova_vm as vm
import guest_identity
import prepare_rootfs


def mbr(data_size=None):
    result = bytearray(512)
    struct.pack_into('<I', result, 440, 0x1234ABCD)
    for index, (start, size, kind) in enumerate([(2048, 131072, 12), (133120, 6553600, 131)]
                                              + ([(6686720, data_size, 131)] if data_size is not None else [])):
        result[446 + index * 16:462 + index * 16] = struct.pack('<B3sB3sII', 128 if index == 0 else 0, b'\xfe\xff\xff', kind, b'\xfe\xff\xff', start, size)
    result[510:] = b'\x55\xaa'
    return bytes(result)


class Board:
    def __init__(self, root, card_bytes=64_000_000_000, mid='1' * 32):
        self.root, self.card_bytes = Path(root), card_bytes
        self.calls, self.failure, self.mounted = [], None, False
        self.kernel_bytes, self.fs_size = 4 * contract.GIB, 4 * contract.GIB
        for name in ('proc/self', 'dev', 'sys/dev/block', 'sys/class/block', 'sys/devices/mock/block/sdz', 'etc', 'srv/xenova', 'run/xenova'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.disk = self.root / 'dev/sdz'
        self.disk.write_bytes(mbr(self.kernel_bytes // 512))
        for number in (2, 3):
            node = self.root / f'sys/devices/mock/block/sdz/sdz{number}'
            node.mkdir()
            (node / 'partition').write_text(str(number))
            (node / 'dev').write_text(f'8:{number}')
            (self.root / f'dev/sdz{number}').write_bytes(b'fixture block node, not an OS disk')
            (self.root / f'sys/dev/block/8:{number}').symlink_to(node)
            (self.root / f'sys/class/block/sdz{number}').symlink_to(node)
        (self.root / 'proc/cmdline').write_text('console=hvc0 root=PARTUUID=1234abcd-02 rootwait')
        (self.root / 'etc/machine-id').write_text(mid + '\n')
        self.set_mount(False)
        self.p = boot.FirstBoot(self.root, self.command, block_devices=False)

    @property
    def data(self):
        return self.root / 'srv/xenova'

    def set_mount(self, mounted):
        self.mounted = mounted
        text = '1 0 8:2 / / rw - ext4 /dev/sdz2 rw\n'
        if mounted:
            text += '2 1 8:3 / /srv/xenova rw - ext4 /dev/sdz3 rw\n'
        (self.root / 'proc/self/mountinfo').write_text(text)

    def table_bytes(self):
        return contract.layout(self.disk.read_bytes(), with_data=True)['partitions'][2]['size'] * 512

    def command(self, args, **kwargs):
        args = [str(a) for a in args]
        self.calls.append((args, kwargs))
        tool = args[0]
        if tool == 'blockdev':
            if args[1] == '--getss':
                return '512\n'
            return str(self.card_bytes if args[-1] == str(self.disk) else self.kernel_bytes) + '\n'
        if tool == 'blkid':
            if 'PARTUUID' in args:
                return '1234abcd-02\n'
            return 'TYPE=ext4\nLABEL=xenova-data\nUUID=aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee\n'
        if tool == 'dumpe2fs':
            return f'Block count: {self.fs_size // 4096}\nBlock size: 4096\n'
        if tool == 'sfdisk':
            start, size = [int(value) for value in re.findall(r'(?:start|size)=(\d+)', kwargs['input'])]
            data = bytearray(self.disk.read_bytes())
            struct.pack_into('<II', data, 486, start, size)
            self.disk.write_bytes(data)
            if self.failure == 'after-partition':
                raise ValueError('injected interruption after partition update')
            return ''
        if tool == 'partx':
            if self.failure == 'partx':
                raise ValueError('injected kernel partition update failure')
            self.kernel_bytes = self.table_bytes()
            return ''
        if tool == 'e2fsck':
            if self.failure == 'fsck':
                raise ValueError('injected filesystem check failure')
            return ''
        if tool == 'resize2fs':
            self.fs_size = self.kernel_bytes // 4096 * 4096
            if self.failure == 'after-resize':
                raise ValueError('injected interruption after filesystem resize')
            return ''
        if tool == 'sha256sum':
            return hashlib.sha256(Path(args[1]).read_bytes()).hexdigest() + '  ' + args[1]
        raise AssertionError('unexpected real command: ' + repr(args))

    def grow_mount(self):
        self.p.grow()
        self.set_mount(True)

    def factory(self):
        for name in ('seeds', 'instances', 'templates'):
            (self.data / name).mkdir()
        records = json.loads((ROOT / 'tests/platform/fixtures/legacy-defaults.json').read_text())['instances']
        bundles, templates = [], []
        for record in records:
            r = copy.deepcopy(record)
            r.update(schema=1, uuid='00000000-0000-4000-8000-000000000000', kernel_release='6.8-fixture', identity_state='pending')
            d = self.data / 'seeds' / r['id']
            d.mkdir()
            for name in ('Image', 'initrd', 'kernel.config', 'root.raw'):
                (d / name).write_bytes(b'fixture only: ' + name.encode())
            info = {'kernel_release': r['kernel_release'], 'root_device': '/dev/xvda',
                    'kernel_sha256': hashlib.sha256((d / 'Image').read_bytes()).hexdigest(),
                    'initrd_sha256': hashlib.sha256((d / 'initrd').read_bytes()).hexdigest(),
                    'config_sha256': hashlib.sha256((d / 'kernel.config').read_bytes()).hexdigest()}
            (d / 'boot.json').write_text(json.dumps(info))
            (d / 'instance.json').write_text(json.dumps(r))
            bundles.append({'record': r, 'root_bytes': (d / 'root.raw').stat().st_size, 'boot': info})
            template = self.data / 'templates' / r['template']
            template.mkdir()
            for name in ('root.raw', 'Image', 'initrd', 'kernel.config', 'boot.json'):
                (template / name).write_bytes((d / name).read_bytes())
                (template / name).chmod(0o444)
            meta = {'schema': 1, 'id': r['template'], 'root_sha256': hashlib.sha256((template / 'root.raw').read_bytes()).hexdigest(), 'source': {'distribution': 'ubuntu' if r['id'] == 'ubuntu-dev' else 'debian'}}
            (template / 'template.json').write_text(json.dumps(meta))
            templates.append({'id': r['template'], 'metadata': meta, 'root_bytes': (template / 'root.raw').stat().st_size})
        factory = {'schema': 1, 'features': contract.LEGACY_FEATURES, 'factory_id': 'fixture-common-factory', 'instances': bundles, 'templates': templates}
        (self.data / '.xenova-install.json').write_text(json.dumps(factory))
        return factory


def platform_root(root):
    """새 Yocto 출력의 파일 계약만 모의한다. 도구 파일은 실행하지 않는 텍스트 fixture다."""
    root = Path(root)
    mapping = {'usr/lib/xenova/xenova_vm.py': FILES / 'xenova_vm.py',
               'etc/xenova/defaults.json': FILES / 'defaults.json',
               'usr/lib/xenova/xenova_first_boot.py': FILES / 'xenova_first_boot.py',
               'usr/lib/xenova/first_install_contract.py': ROOT / 'platform/first-install/first_install_contract.py',
               'usr/sbin/xenova-first-boot': FILES / 'xenova-first-boot',
               'usr/sbin/xenova-vm': FILES / 'xenova-vm',
               'usr/sbin/xenova-verify-artifacts': ROOT / 'scripts/verify-xenova-runtime-artifacts.py',
               'usr/lib/xenova/xenova_runtime.py': FILES / 'xenova_runtime.py',
               'usr/lib/xenova/xenova_zephyr.py': FILES / 'xenova_zephyr.py',
               'etc/systemd/journald.conf.d/20-xenova.conf': FILES / 'xenova-journald.conf',
               'etc/systemd/system.conf.d/20-xenova.conf': FILES / 'xenova-system.conf'}
    for name in ('xenova_management.py','xenova_management_cli.py','xenova_catalog.py','xenova_wifi.py',
                 'xenova_execution.py','xenova_recovery.py','xenova_domain_identity.py'):
        mapping['usr/lib/xenova/'+name] = FILES/name
    mapping['usr/lib/xenova/artifact_io.py'] = ROOT/'platform/first-install/artifact_io.py'
    mapping['usr/sbin/xenova-wifi'] = FILES/'xenova-wifi'
    mapping['usr/sbin/xenova-domain-identity'] = FILES/'xenova-domain-identity'
    mapping['etc/xenova/execution-policy.json'] = FILES/'execution-policy.json'
    mapping['usr/lib/xenova/update_guest_ssh.py'] = ROOT / 'scripts/update-xenova-guest-ssh.py'
    for name in ('prepare_instance.py', 'prepare_rootfs.py', 'guest_identity.py'):
        mapping['usr/lib/xenova/' + name] = ROOT / 'platform/guests/common' / name
    for path in [*FILES.glob('*.service'), *FILES.glob('*.mount')]:
        mapping['usr/lib/systemd/system/' + path.name] = path
    for relative, source in mapping.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    library = root/'usr/lib/libxenmanage.so.1'
    library.parent.mkdir(parents=True, exist_ok=True)
    library.write_text('packaging fixture: not a target binary')
    cap = root / 'usr/share/xenova/first-install-capabilities.json'
    cap.parent.mkdir(parents=True)
    cap.write_text(json.dumps(contract.FEATURES))
    (root / 'etc/machine-id').write_text('uninitialized\n')
    (root / 'etc/ssh').mkdir()
    from dom0_ssh_fixture import install_policy
    install_policy(root)
    keys = root / 'usr/lib/systemd/system/sshdgenkeys.service'
    keys.write_text('[Service]\\nExecStart=/usr/libexec/openssh/sshd_check_keys\\n')
    checker = root / 'usr/libexec/openssh/sshd_check_keys'
    checker.parent.mkdir(parents=True); checker.write_text('packaging fixture: not executed')
    wants = root / 'etc/systemd/system/multi-user.target.wants'
    wants.mkdir(exist_ok=True)
    for name in ('xenova-host-admission.service', 'xenova-first-boot.service', 'srv-xenova.mount', 'xenova-vms.service', 'xenova-vm-shutdown.service'):
        (wants / name).symlink_to('/usr/lib/systemd/system/' + name)
    tools = root / 'usr/bin'
    tools.mkdir(parents=True)
    for name in ('sfdisk', 'partx', 'blockdev', 'blkid', 'e2fsck', 'resize2fs', 'dumpe2fs', 'tune2fs', 'losetup', 'mount', 'umount', 'findmnt', 'cp', 'chroot', 'ssh-keygen', 'lsblk', 'ip', 'ss', 'timedatectl', 'qemu-system-i386', 'wpa_supplicant', 'wpa_cli', 'iw', 'networkctl'):
        (tools / name).write_text('fixture: not executed')
        (tools / name).chmod(0o755)
    for relative in ('brcm/brcmfmac43455-sdio.bin','brcm/brcmfmac43455-sdio.clm_blob',
                     'brcm/brcmfmac43455-sdio.raspberrypi,4-model-b.txt','regulatory.db','regulatory.db.p7s'):
        target=root/'usr/lib/firmware'/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(b'fixture: not real firmware')
    for name, text in [('qemu-config-host.h', '#define CONFIG_VNC 1\n#define CONFIG_XEN_BACKEND 1\n'),
                       ('qemu-i386-config-target.h', '#define CONFIG_XEN 1\n')]:
        (root / 'usr/share/xenova' / name).write_text(text)
    return root


def profile_dts():
    """드라이버 실행 대신 정적 프로필의 cell 검증만 하는 작은 DTS fixture."""
    return '''/dts-v1/;
/ {
 cpu@0 { reg = <0x0>; };
 memory@40000000 { reg = <0 0x40000000 0 0x1000000>; };
 interrupt-controller@3001000 {
  #interrupt-cells = <4>;
  reg = <0 0x3001000 0 0x1000 0 0x3002000 0 0x2000>;
 };
 hypervisor@38000000 {
  reg = <0 0x38000000 0 0x1000000>;
  interrupts = <1 15 4 160>;
 };
 timer { interrupts = <1 13 2 160 1 14 2 160 1 11 2 160>; };
 hvc { compatible = "xen,hvc-uart"; };
};
'''

def starter_bundle(target):
    """현재 Starter 출처 계약을 만족하는 작은 시험용 펌웨어 번들."""
    from test_xenova_lifecycle import application_bundle, bundle
    target = Path(target)
    source = application_bundle(bundle(target.parent / (target.name + '-diagnostic')), target)
    manifest = source / 'firmware.json'
    meta = json.loads(manifest.read_text())
    lock = json.loads((ROOT / 'platform/guests/zephyr/starter.lock.json').read_text())
    meta.update(id=lock['id'], kind='zephyr-xen-firmware', version=lock['version'])
    meta['source'].update({k: lock[k] for k in ('app','app_files','app_source_sha256','zephyr_commit','sdk_version')})
    meta['source']['source_revision'] = 'sha256:' + lock['app_source_sha256']
    manifest.chmod(0o644)
    manifest.write_text(json.dumps(meta))
    manifest.chmod(0o444)
    return source
