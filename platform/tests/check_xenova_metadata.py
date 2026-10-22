"""분리된 bitbake -e 출력에서 실제 레시피 연결·인증·자동 시작 정책을 검사한다."""
from pathlib import Path
from fnmatch import fnmatchcase
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
LAYER = ROOT / 'platform/yocto/meta-xenova'
logs = Path(sys.argv[1])


def load(target):
    text = (logs / (target + '.env')).read_text()
    return text, dict(re.findall(r'^(?:export )?([A-Za-z0-9_:/.-]+)="(.*)"$', text, re.M))


def selected(values, name, *, directory=False):
    return next(Path(p) / name for p in values['FILESPATH'].split(':')
                if ((Path(p) / name).is_dir() if directory else (Path(p) / name).is_file()))


_, boot = load('xen-rpi-u-boot-scr')
assert selected(boot, 'boot.cmd.xen.in') == LAYER / 'recipes-bsp/xen-rpi-u-boot-scr/files/boot.cmd.xen.in'
assert boot['S'] == boot['UNPACKDIR']
assert boot['RPI_DOM0_MEM'] == '1024M'
_, xen = load('xen-tools')
assert '10-xenbr0.netdev' not in xen['VIRT_NETWORKING_FILES']
assert 'file://10-xenbr0.netdev' not in xen['SRC_URI'].split()
for filename in ['10-ether.network', '10-xenbr0.network']:
    assert selected(xen, filename) == LAYER / 'recipes-extended/xen/files' / filename
text, bringup = load('xenova-bringup')
assert bringup['SYSTEMD_AUTO_ENABLE:xenova-bringup'] == 'enable'
assert "sed -e 's|@BINDIR@|/usr/bin|g'" in text
platform_text, platform = load('xenova-platform')
assert platform['SYSTEMD_AUTO_ENABLE:xenova-platform'] == 'enable'
assert set(platform['SYSTEMD_SERVICE:xenova-platform'].split()) == {'srv-xenova.mount', 'xenova-vm-shutdown.service', 'xenova-vms.service', 'xenova-first-boot.service', 'xenova-host-admission.service'}
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
    assert owners and owners[0] == 'xenova-platform', f'unowned or incorrectly owned unit: {unit}: {owners}'
assert 'xenova-xen-ready.service' not in platform['SYSTEMD_SERVICE:xenova-platform'].split()
assert 'xenova-data-prepare.service' not in platform['SYSTEMD_SERVICE:xenova-platform'].split()
assert selected(platform, 'first_install_contract.py').resolve() == ROOT / 'platform/first-install/first_install_contract.py'
assert selected(platform, 'xenova-wifi-modprobe.conf') == LAYER / 'recipes-core/xenova-platform/files/xenova-wifi-modprobe.conf'
assert '/etc/modprobe.d/xenova-wifi.conf' in platform['FILES:xenova-platform'].split()
assert '/etc/modprobe.d/xenova-wifi.conf' in platform['CONFFILES:xenova-platform'].split()
for module in ('prepare_instance.py', 'prepare_rootfs.py', 'guest_identity.py'):
    assert selected(platform, module).resolve() == ROOT / 'platform/guests/common' / module
assert 'installed-vs-shipped' in platform['ERROR_QA'].split()
for name, value in platform.items():
    if name == 'INSANE_SKIP' or name.startswith('INSANE_SKIP:'):
        assert 'installed-vs-shipped' not in value.split(), f'QA disabled: {name}'
print('PASS unit ownership: ' + ', '.join(installed_units) + '; static unit not enabled; installed-vs-shipped QA retained')
required = {'kernel-module-brcmfmac', 'kernel-module-brcmfmac-cyw', 'python3-core', 'python3-json', 'python3-ctypes', 'xen-tools-libxenmanage', 'iproute2', 'xen-tools-xl', 'xen-tools-xenstore', 'openssh-sshd',
            'util-linux-sfdisk', 'util-linux-partx', 'util-linux-blockdev', 'util-linux-blkid',
            'util-linux-losetup', 'util-linux-mount', 'util-linux-umount', 'util-linux-findmnt',
            'e2fsprogs-e2fsck', 'e2fsprogs-resize2fs', 'e2fsprogs-dumpe2fs', 'e2fsprogs-tune2fs', 'coreutils'}
assert 'xen-tools-libxenmanage' in xen['PACKAGES'].split()
assert xen['FILES:xen-tools-libxenmanage'] == '/usr/lib/libxenmanage.so.*'
missing = required - set(platform['RDEPENDS:xenova-platform'].split())
assert not missing, f'Required runtime packages are undeclared: {sorted(missing)}'
text, image = load('xenova-xen-bringup-image')
assert 'xen-' + image['MACHINE'] + ';xen' in image['IMAGE_BOOT_FILES'].split()
assert 'xen:do_deploy' in (logs / 'wic-depends.txt').read_text().split()
print('PASS WIC: Xen deploy file maps to xen, with explicit do_deploy dependency')
assert not {'empty-root-password', 'allow-empty-password', 'debug-tweaks', 'serial-autologin-root'} & set(image['IMAGE_FEATURES'].split())
assert 'usermod -p' in image['EXTRA_USERS_PARAMS'] and 'perseus-dev' in image['EXTRA_USERS_PARAMS']
assert 'xenova_platform_policy' in image['ROOTFS_POSTPROCESS_COMMAND']
assert 'xenova-platform' in image['IMAGE_INSTALL'].split()
assert 'tzdata-asia' in image['IMAGE_INSTALL'].split()
assert 'xenova_set_timezone' in image['ROOTFS_POSTPROCESS_COMMAND']
assert re.search(r'^xenova_set_timezone\s*\(\)\s*\{', text, re.M)
for module in ('xenova_vm.py', 'xenova_runtime.py', 'xenova_wifi.py',
               'xenova_resources.py', 'xenova_zephyr_transition.py', 'xenova_zephyr_control.py',
               'xenova_cpu.py', 'xenova_execution.py', 'xenova_recovery.py', 'xenova_domain_identity.py'):
    assert 'file://' + module in platform['SRC_URI'].split(), module
    assert selected(platform, module) == LAYER / 'recipes-core/xenova-platform/files' / module
print('PASS reviewed runtime source selection and Asia/Seoul image postprocess dependency')
assert "printf 'uninitialized\\n'" in text and 'ln -snf /etc/machine-id' in text
assert 'ln -snf /dev/null' in text and 'xendomains.service' in text
assert 'Include /etc/ssh/xenova-auth.conf' in text
print('PASS metadata: selected boot/network sources, DHCP bindir, enabled services, JSON dependency, nonempty password and single autostart owner')

_, qemu = load('qemu')
assert qemu['PV'] == '10.2.0'
assert {'pixman', 'vnc', 'xen'} <= set(qemu['PACKAGECONFIG'].split())
for flag in ('--enable-pixman', '--enable-vnc', '--enable-xen'):
    assert flag in qemu['PACKAGECONFIG_CONFARGS'].split(), flag
for flag in ('--disable-pixman', '--disable-vnc', '--disable-xen'):
    assert flag not in qemu['PACKAGECONFIG_CONFARGS'].split(), flag
assert 'i386-softmmu' in qemu['EXTRA_OECONF']
assert {'python3-crypt', 'iproute2-ss', 'util-linux-lsblk'} <= set(platform['RDEPENDS:xenova-platform'].split())
for module in ('xenova_runtime.py', 'xenova_zephyr.py'):
    assert selected(platform, module) == LAYER / 'recipes-core/xenova-platform/files' / module
assert selected(platform, 'verify-xenova-runtime-artifacts.py').resolve() == ROOT / 'platform/scripts/verify-xenova-runtime-artifacts.py'
for header in ('qemu-config-host.h', 'qemu-i386-config-target.h'):
    assert header in (LAYER / 'recipes-devtools/qemu/qemu_10.2.0.bbappend').read_text()
print('PASS frozen QEMU target: VNC/pixman/Xen enabled, i386 target retained, no listener configured')


# 이름 변경 후 실제 PN·SRC_URI·서비스·이미지 선행 조건까지 이어지는지 확인한다.
for target in ('xenova-bringup', 'xenova-platform', 'xenova-domu-init', 'xenova-domu-initramfs', 'xenova-xen-bringup-image'):
    _, values = load(target)
    assert values['PN'] == target
    assert 'xenova' in values['BBFILE_COLLECTIONS'].split()
    assert 'perseus' not in values['BBFILE_COLLECTIONS'].split()
    for entry in values.get('SRC_URI', '').split():
        if entry.startswith('file://'):
            name = entry[7:].split(';', 1)[0]
            if target == 'xenova-platform' and name in ('ubuntu', 'debian'):
                source = selected(values, name, directory=True)
                assert source.resolve() == ROOT / 'platform/guests' / name
                assert not source.is_symlink() and (source / 'xen.cfg.in').is_file()
            else:
                assert selected(values, name).is_file(), (target, name)
_, init = load('xenova-domu-initramfs')
assert 'xenova-domu-init' in init['PACKAGE_INSTALL'].split()
assert 'xenova-domu-initramfs:do_image_complete' in text
_, ssh = load('openssh')
assert 'systemd-sshd-service-mode' in ssh['PACKAGECONFIG'].split()
assert 'systemd-sshd-socket-mode' not in ssh['PACKAGECONFIG'].split()
assert ssh['SYSTEMD_SERVICE:openssh-sshd'].split() == ['sshd.service']
assert 'systemd-ssh-generator' in platform_text
assert 'sshd.service.d/10-xenova-keys.conf' in platform_text
assert '/srv/xenova' in platform['FILES:xenova-platform'].split()
print('PASS Xenova PN/SRC_URI/image dependencies, single SSH service mode and new storage ownership')

# Installed applications must be real fixed-layer packages, not WIC injections.
_, api = load('xenova-dom0-manager')
_, mqtt = load('xenova-mqtt-client')
assert {'xenova-dom0-manager', 'xenova-mqtt-client'} <= set(image['IMAGE_INSTALL'].split())
assert {'python3-fastapi', 'python3-uvicorn', 'python3-websockets', 'python3-terminal', 'python3-netclient', 'python3-shell', 'python3-datetime', 'xenova-platform', 'xen-tools-xenstat', 'xenova-mqtt-client'} <= set(api['RDEPENDS:xenova-dom0-manager'].split())
assert 'python3-paho-mqtt' in mqtt['RDEPENDS:xenova-mqtt-client'].split()
assert set(api['SYSTEMD_SERVICE:xenova-dom0-manager'].split()) == {'xenova-dom0-manager.service', 'xenova-recovery-worker.service'}
assert selected(api, 'xenova-recovery-worker.service').resolve() == ROOT/'platform/services/dom0-manager/deploy/xenova-recovery-worker.service'
assert api['SYSTEMD_AUTO_ENABLE:xenova-dom0-manager'] == 'enable'
assert selected(api, 'dom0_manager', directory=True).resolve() == ROOT/'platform/services/dom0-manager/dom0_manager'
assert selected(mqtt, 'xenova_mqtt', directory=True).resolve() == ROOT/'platform/services/vm-agent/mqtt-client/xenova_mqtt'
print('PASS FastAPI/uvicorn/platform driver and MQTT recipe/source/runtime connection')


# SSH로 통계/감시기 시험 소스를 옮길 때 필요한 runtime과 broker 도구를 미리 설치한다.
assert 'xen-tools-xenstat' in xen['PACKAGES'].split()
assert '/usr/sbin/xentop' in xen['FILES:xen-tools-xenstat'].split()
_, broker = load('mosquitto')
assert {'mosquitto', 'mosquitto-clients'} <= set(image['IMAGE_INSTALL'].split())
assert broker['SYSTEMD_SERVICE:mosquitto'] == 'mosquitto.service'
assert broker['SYSTEMD_AUTO_ENABLE:mosquitto'] == 'disable'
assert {'/usr/bin/mosquitto_pub', '/usr/bin/mosquitto_sub', '/usr/bin/mosquitto_rr'} <= set(broker['FILES:mosquitto-clients'].split())
print('PASS xentop/MQTT runtime packages and broker tools; broker activation remains explicit')

# CPU preparation is static and pulled before guests, not a second autostart owner.
assert 'file://cpu-policy-preset.json' in platform['SRC_URI'].split()
assert selected(platform, 'cpu-policy-preset.json') == LAYER/'recipes-core/xenova-platform/files/cpu-policy-preset.json'
assert '/usr/lib/systemd/system/xenova-cpu-prepare.service' in platform['FILES:xenova-platform'].split()
assert 'xenova-cpu-prepare.service' not in platform['SYSTEMD_SERVICE:xenova-platform'].split()
assert '/etc/xenova/cpu-policy-preset.json' in platform['CONFFILES:xenova-platform'].split()
print('PASS CPU module, factory preset and static prepare unit package ownership')
