"""분리된 bitbake -e 출력에서 실제 레시피 연결·인증·자동 시작 정책을 검사한다."""
from pathlib import Path
from fnmatch import fnmatchcase
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
LAYER = ROOT / 'platform/yocto/meta-zenova'
logs = Path(sys.argv[1])


def load(target):
    text = (logs / (target + '.env')).read_text()
    return text, dict(re.findall(r'^(?:export )?([A-Za-z0-9_:/.-]+)="(.*)"$', text, re.M))


def selected(values, name):
    return next(Path(p) / name for p in values['FILESPATH'].split(':') if (Path(p) / name).is_file())


_, boot = load('xen-rpi-u-boot-scr')
assert selected(boot, 'boot.cmd.xen.in') == LAYER / 'recipes-bsp/xen-rpi-u-boot-scr/files/boot.cmd.xen.in'
assert boot['S'] == boot['UNPACKDIR']
assert boot['RPI_DOM0_MEM'] == '1024M'
_, xen = load('xen-tools')
assert '10-xenbr0.netdev' not in xen['VIRT_NETWORKING_FILES']
assert 'file://10-xenbr0.netdev' not in xen['SRC_URI'].split()
for filename in ['10-ether.network', '10-xenbr0.network']:
    assert selected(xen, filename) == LAYER / 'recipes-extended/xen/files' / filename
text, bringup = load('zenova-bringup')
assert bringup['SYSTEMD_AUTO_ENABLE:zenova-bringup'] == 'enable'
assert "sed -e 's|@BINDIR@|/usr/bin|g'" in text
platform_text, platform = load('zenova-platform')
assert platform['SYSTEMD_AUTO_ENABLE:zenova-platform'] == 'enable'
assert set(platform['SYSTEMD_SERVICE:zenova-platform'].split()) == {'srv-zenova.mount', 'zenova-vm-shutdown.service', 'zenova-vms.service', 'zenova-first-boot.service'}
# package task는 실행하지 않는다. 실제 해석된 install 목록과 모든 패키지의 FILES를 대조한다.
installed_units = re.search(r'for unit in ([^;]+); do', platform_text).group(1).split()
for unit in installed_units:
    unit_path = Path(platform['systemd_system_unitdir']) / unit
    owners = []
    for package in platform['PACKAGES'].split():
        patterns = platform.get('FILES:' + package, '').split()
        if any(fnmatchcase(str(parent), pattern) for parent in [unit_path, *unit_path.parents]
               for pattern in patterns):
            owners.append(package)
    assert owners and owners[0] == 'zenova-platform', f'unowned or incorrectly owned unit: {unit}: {owners}'
assert 'zenova-xen-ready.service' not in platform['SYSTEMD_SERVICE:zenova-platform'].split()
assert 'zenova-data-prepare.service' not in platform['SYSTEMD_SERVICE:zenova-platform'].split()
assert selected(platform, 'first_install_contract.py').resolve() == ROOT / 'platform/first-install/first_install_contract.py'
for module in ('prepare_instance.py', 'prepare_rootfs.py', 'guest_identity.py'):
    assert selected(platform, module).resolve() == ROOT / 'platform/guests/common' / module
assert 'installed-vs-shipped' in platform['ERROR_QA'].split()
for name, value in platform.items():
    if name == 'INSANE_SKIP' or name.startswith('INSANE_SKIP:'):
        assert 'installed-vs-shipped' not in value.split(), f'QA disabled: {name}'
print('PASS unit ownership: ' + ', '.join(installed_units) + '; static unit not enabled; installed-vs-shipped QA retained')
required = {'python3-core', 'python3-json', 'iproute2', 'xen-tools-xl', 'openssh-sshd',
            'util-linux-sfdisk', 'util-linux-partx', 'util-linux-blockdev', 'util-linux-blkid',
            'util-linux-losetup', 'util-linux-mount', 'util-linux-umount', 'util-linux-findmnt',
            'e2fsprogs-e2fsck', 'e2fsprogs-resize2fs', 'e2fsprogs-dumpe2fs', 'e2fsprogs-tune2fs', 'coreutils'}
missing = required - set(platform['RDEPENDS:zenova-platform'].split())
assert not missing, f'Required runtime packages are undeclared: {sorted(missing)}'
text, image = load('zenova-xen-bringup-image')
assert 'xen-' + image['MACHINE'] + ';xen' in image['IMAGE_BOOT_FILES'].split()
assert 'xen:do_deploy' in (logs / 'wic-depends.txt').read_text().split()
print('PASS WIC: Xen deploy file maps to xen, with explicit do_deploy dependency')
assert not {'empty-root-password', 'allow-empty-password', 'debug-tweaks', 'serial-autologin-root'} & set(image['IMAGE_FEATURES'].split())
assert 'usermod -p' in image['EXTRA_USERS_PARAMS'] and 'perseus-dev' in image['EXTRA_USERS_PARAMS']
assert 'zenova_platform_policy' in image['ROOTFS_POSTPROCESS_COMMAND']
assert 'zenova-platform' in image['IMAGE_INSTALL'].split()
assert "printf 'uninitialized\\n'" in text and 'ln -snf /etc/machine-id' in text
assert 'ln -snf /dev/null' in text and 'xendomains.service' in text
assert 'Include /etc/ssh/zenova-auth.conf' in text
print('PASS metadata: selected boot/network sources, DHCP bindir, enabled services, JSON dependency, nonempty password and single autostart owner')

_, qemu = load('qemu')
assert qemu['PV'] == '10.2.0'
assert {'pixman', 'vnc', 'xen'} <= set(qemu['PACKAGECONFIG'].split())
for flag in ('--enable-pixman', '--enable-vnc', '--enable-xen'):
    assert flag in qemu['PACKAGECONFIG_CONFARGS'].split(), flag
for flag in ('--disable-pixman', '--disable-vnc', '--disable-xen'):
    assert flag not in qemu['PACKAGECONFIG_CONFARGS'].split(), flag
assert 'i386-softmmu' in qemu['EXTRA_OECONF']
assert {'python3-crypt', 'iproute2-ss', 'util-linux-lsblk'} <= set(platform['RDEPENDS:zenova-platform'].split())
for module in ('zenova_runtime.py', 'zenova_zephyr.py'):
    assert selected(platform, module) == LAYER / 'recipes-core/zenova-platform/files' / module
assert selected(platform, 'verify-zenova-runtime-artifacts.py').resolve() == ROOT / 'scripts/verify-zenova-runtime-artifacts.py'
for header in ('qemu-config-host.h', 'qemu-i386-config-target.h'):
    assert header in (LAYER / 'recipes-devtools/qemu/qemu_10.2.0.bbappend').read_text()
print('PASS frozen QEMU target: VNC/pixman/Xen enabled, i386 target retained, no listener configured')


# 이름 변경 후 실제 PN·SRC_URI·서비스·이미지 선행 조건까지 이어지는지 확인한다.
for target in ('zenova-bringup', 'zenova-platform', 'zenova-domu-init', 'zenova-domu-initramfs', 'zenova-xen-bringup-image'):
    _, values = load(target)
    assert values['PN'] == target
    assert 'zenova' in values['BBFILE_COLLECTIONS'].split()
    assert 'perseus' not in values['BBFILE_COLLECTIONS'].split()
    for entry in values.get('SRC_URI', '').split():
        if entry.startswith('file://'):
            name = entry[7:].split(';', 1)[0]
            assert selected(values, name).is_file(), (target, name)
_, init = load('zenova-domu-initramfs')
assert 'zenova-domu-init' in init['PACKAGE_INSTALL'].split()
assert 'zenova-domu-initramfs:do_image_complete' in text
_, ssh = load('openssh')
assert 'systemd-sshd-service-mode' in ssh['PACKAGECONFIG'].split()
assert 'systemd-sshd-socket-mode' not in ssh['PACKAGECONFIG'].split()
assert ssh['SYSTEMD_SERVICE:openssh-sshd'].split() == ['sshd.service']
assert 'systemd-ssh-generator' in platform_text
assert 'sshd.service.d/10-zenova-keys.conf' in platform_text
assert '/srv/zenova' in platform['FILES:zenova-platform'].split()
print('PASS Zenova PN/SRC_URI/image dependencies, single SSH service mode and new storage ownership')
