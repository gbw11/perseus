"""분리된 bitbake -e 출력에서 실제 레시피 연결·인증·자동 시작 정책을 검사한다."""
from pathlib import Path
from fnmatch import fnmatchcase
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
LAYER = ROOT / 'platform/yocto/meta-perseus'
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
text, bringup = load('perseus-bringup')
assert bringup['SYSTEMD_AUTO_ENABLE:perseus-bringup'] == 'enable'
assert "sed -e 's|@BINDIR@|/usr/bin|g'" in text
platform_text, platform = load('perseus-platform')
assert platform['SYSTEMD_AUTO_ENABLE:perseus-platform'] == 'enable'
assert set(platform['SYSTEMD_SERVICE:perseus-platform'].split()) == {'srv-perseus.mount', 'perseus-vm-shutdown.service', 'perseus-vms.service'}
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
    assert owners and owners[0] == 'perseus-platform', f'unowned or incorrectly owned unit: {unit}: {owners}'
assert 'perseus-xen-ready.service' not in platform['SYSTEMD_SERVICE:perseus-platform'].split()
assert 'installed-vs-shipped' in platform['ERROR_QA'].split()
for name, value in platform.items():
    if name == 'INSANE_SKIP' or name.startswith('INSANE_SKIP:'):
        assert 'installed-vs-shipped' not in value.split(), f'QA disabled: {name}'
print('PASS unit ownership: ' + ', '.join(installed_units) + '; static unit not enabled; installed-vs-shipped QA retained')
required = {'python3-core', 'python3-json', 'iproute2', 'xen-tools-xl', 'openssh-sshd'}
missing = required - set(platform['RDEPENDS:perseus-platform'].split())
assert not missing, f'Required runtime packages are undeclared: {sorted(missing)}'
text, image = load('perseus-xen-bringup-image')
assert 'xen-' + image['MACHINE'] + ';xen' in image['IMAGE_BOOT_FILES'].split()
assert 'xen:do_deploy' in (logs / 'wic-depends.txt').read_text().split()
print('PASS WIC: Xen deploy file maps to xen, with explicit do_deploy dependency')
assert not {'empty-root-password', 'allow-empty-password', 'debug-tweaks', 'serial-autologin-root'} & set(image['IMAGE_FEATURES'].split())
assert 'usermod -p' in image['EXTRA_USERS_PARAMS'] and 'perseus-dev' in image['EXTRA_USERS_PARAMS']
assert 'perseus_platform_policy' in image['ROOTFS_POSTPROCESS_COMMAND']
assert 'perseus-platform' in image['IMAGE_INSTALL'].split()
assert 'ln -snf /dev/null' in text and 'xendomains.service' in text
assert 'Include /etc/ssh/perseus-auth.conf' in text
print('PASS metadata: selected boot/network sources, DHCP bindir, enabled services, JSON dependency, nonempty password and single autostart owner')
