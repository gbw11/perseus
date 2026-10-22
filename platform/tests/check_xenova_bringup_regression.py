"""임시 경로와 명령 대역만 사용하며 실제 mount/NIC/부팅을 수행하지 않는다."""
import configparser
import fnmatch
import os
import sys
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2] / 'platform/yocto/meta-xenova'
boot = (ROOT / 'recipes-bsp/xen-rpi-u-boot-scr/files/boot.cmd.xen.in').read_text()
# U-Boot 셀 표기는 호스트 셸 대역에서 문자열로 전달한다.
boot = boot.replace('<${kernel_loadaddr} ${kernel_size}>', '"<${kernel_loadaddr} ${kernel_size}>"').replace('<1>', "'<1>'")
boot = boot.replace('@@RPI_DOM0_MEM@@', '1024M').replace('@@RPI_DEBUG_XEN_ARGS@@', 'sync_console bootscrub=0')
boot_mocks = r'''
setenv() { key=$1; shift; export "$key=$*"; }
fdt() { echo "FDT $*"; [ "${FAIL:-}" != "fdt-$1" ] && [ "${FAIL:-}" != "fdt-$1-${3:-}" ]; }
part() { echo "PART $*"; [ "${FAIL:-}" != part ] || return 1; rootfs_partuuid=test-root; }
fatload() { echo "LOAD $*"; [ "${FAIL:-}" != "$4" ] || return 1; filesize=1234; }
booti() { echo "BOOT $*"; }
sleep() { :; }
'''
for name, values, failure in [
    ('bootflow-mmc0', {'devtype': 'mmc', 'devnum': '0'}, False),
    ('bootflow-mmc1', {'devtype': 'mmc', 'devnum': '1'}, False),
    ('manual-mmc0-fallback', {}, False),
    ('reject-usb', {'devtype': 'usb', 'devnum': '0'}, True),
    ('stop-on-fdt-copy-failure', {'FAIL': 'fdt-move'}, True),
    ('stop-on-sdio-dmas-removal-failure', {'FAIL': 'fdt-rm-dmas'}, True),
    ('stop-on-sdio-dma-names-removal-failure', {'FAIL': 'fdt-rm-dma-names'}, True),
    ('stop-on-partuuid-failure', {'FAIL': 'part'}, True),
    ('stop-on-xen-load-failure', {'FAIL': 'xen'}, True),
    ('stop-on-kernel-load-failure', {'FAIL': 'Image'}, True),
]:
    env = {**os.environ, 'devtype': '', 'devnum': '', 'FAIL': '', 'fdtcontroladdr': '0x3aff0000', 'fdt_addr_r': '0x05600000', **values}
    result = subprocess.run(['bash', '-c', boot_mocks + boot], env=env, text=True, capture_output=True, timeout=5)
    assert (result.returncode != 0) == failure, (name, result.stdout, result.stderr)
    if failure:
        assert 'BOOT ' not in result.stdout, (name, result.stdout)
    else:
        devnum = values.get('devnum', '0')
        assert f'PART uuid mmc {devnum}:2 rootfs_partuuid' in result.stdout
        assert f'LOAD mmc {devnum}:1 0x00200000 xen' in result.stdout
        assert f'LOAD mmc {devnum}:1 0x00400000 Image' in result.stdout
        assert 'BOOT 0x00200000 - 0x05600000' in result.stdout
        assert result.stdout.index('FDT move 0x3aff0000 0x05600000') < result.stdout.index('FDT set ')
        assert result.stdout.index('FDT move ') < result.stdout.index('FDT rm /soc/mmcnr@7e300000 dmas')
        assert result.stdout.index('FDT rm /soc/mmcnr@7e300000 dmas') < result.stdout.index('FDT rm /soc/mmcnr@7e300000 dma-names')
        assert result.stdout.index('FDT rm /soc/mmcnr@7e300000 dma-names') < result.stdout.index('LOAD ')
        assert 'FDT rm /soc/mmc@7e340000' not in result.stdout
        assert 'FDT rm /soc/dma' not in result.stdout
    print('PASS', name)

init = (ROOT / 'recipes-core/xenova-domu-init/files/init').read_text().split('\nwhile true; do', 1)[0]
init_mocks = r'''
mock_mount() {
    echo "MOUNT $2 $4"
    [ -d "$4" ] || return 1
    [ "$FAIL_MOUNT" != "$2" ] || return 1
    if [ "$2" = sysfs ] && [ "$NIC_DELAY" -eq 0 ]; then mkdir -p "$TEST_ROOT/sys/class/net/eth0"; fi
}
uname() { :; }
ip() {
    echo "IP $*"
    [ "$*" != 'link set eth0 up' ] || [ "$FAIL_LINK" -eq 0 ]
}
sleep() {
    waits=$((waits + 1))
    echo "WAIT $waits"
    if [ "$waits" -eq "$NIC_DELAY" ]; then mkdir -p "$TEST_ROOT/sys/class/net/eth0"; fi
}
udhcpc() { echo "DHCP $*"; return "$DHCP_RESULT"; }
waits=0
'''
for name, values, dhcp, waits in [
    ('nic-ready-after-mounts', {}, True, 0),
    ('nic-delayed-3s', {'NIC_DELAY': '3'}, True, 3),
    ('nic-missing-bounded-20s', {'NIC_DELAY': '999'}, False, 20),
    ('proc-mount-failure', {'FAIL_MOUNT': 'proc'}, False, 0),
    ('sysfs-mount-failure', {'FAIL_MOUNT': 'sysfs'}, False, 0),
    ('devtmpfs-mount-failure', {'FAIL_MOUNT': 'devtmpfs'}, False, 0),
    ('eth0-up-failure', {'FAIL_LINK': '1'}, False, 0),
    ('dhcp-failure-reaches-shell', {'DHCP_RESULT': '1'}, True, 0),
]:
    with tempfile.TemporaryDirectory(prefix='xenova-init-test-') as temp:
        script = re.sub(r'/(proc|sys|dev|run|tmp)(?=[/\s])', lambda m: temp + m.group(), init)
        script = script.replace('/bin/mount', 'mock_mount')
        env = {**os.environ, 'TEST_ROOT': temp, 'NIC_DELAY': '0', 'FAIL_MOUNT': '', 'FAIL_LINK': '0', 'DHCP_RESULT': '0', **values}
        result = subprocess.run(['sh', '-c', init_mocks + script], env=env, text=True, capture_output=True, timeout=5)
        assert result.returncode == 0, (name, result.stdout, result.stderr)
        assert ('DHCP -f ' in result.stdout) == dhcp, (name, result.stdout, result.stderr)
        assert result.stdout.count('WAIT ') == waits, (name, result.stdout)
        assert 'Minimal guest shell' in result.stdout
        if dhcp:
            assert result.stdout.index('MOUNT devtmpfs') < result.stdout.index('IP link set eth0 up') < result.stdout.index('DHCP -f ')
            assert '-n -q -t 5 -T 2' in result.stdout
        if values.get('DHCP_RESULT') == '1':
            assert 'DHCP did not succeed' in result.stdout
        print('PASS', name)

rules = []
for filename in ['10-ether.network', '10-xenbr0.network']:
    config = configparser.ConfigParser()
    config.read(ROOT / 'recipes-extended/xen/files' / filename)
    assert config['Link']['Unmanaged'] == 'yes'
    assert 'Network' not in config
    rules.append(config['Match']['Name'])
assert not any(fnmatch.fnmatchcase('eth0', pattern) for pattern in rules)
assert any(fnmatch.fnmatchcase('vif1.0', pattern) for pattern in rules)
assert any(fnmatch.fnmatchcase('xenbr0', pattern) for pattern in rules)
print('PASS network-rules-match-only-vif-and-xenbr0')
print('PASS 19 boot/init/network regression cases (hardware not exercised)')
