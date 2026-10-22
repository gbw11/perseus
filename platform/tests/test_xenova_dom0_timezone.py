"""시간대 패키징의 실제 rootfs 후처리와 외부망 대기 분리를 확인한다."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from first_install_fixture import ROOT


class Dom0TimezoneTest(unittest.TestCase):
    def test_image_policy_installs_seoul_link_and_rejects_missing_asset(self):
        recipe = (ROOT / 'platform/yocto/meta-xenova/recipes-core/images/xenova-xen-bringup-image.bb').read_text()
        self.assertRegex(recipe, r'IMAGE_INSTALL:append = "[^"]* tzdata-asia"')
        function = re.search(r'(xenova_set_timezone\(\) \{.*?^\})', recipe, re.M | re.S).group(1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = ('bbfatal() { echo "$*" >&2; exit 1; }\n' +
                      function.replace('${IMAGE_ROOTFS}', directory).replace('${datadir}', '/usr/share')
                              .replace('${sysconfdir}', '/etc') + '\nxenova_set_timezone\n')
            failed = subprocess.run(['sh', '-eu', '-c', script], capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertFalse((root / 'etc/localtime').exists())
            zone = root / 'usr/share/zoneinfo/Asia/Seoul'
            zone.parent.mkdir(parents=True)
            zone.write_bytes(b'TZif-packaging-fixture')
            passed = subprocess.run(['sh', '-eu', '-c', script], capture_output=True, text=True)
            self.assertEqual(passed.returncode, 0, passed.stderr)
            self.assertEqual((root / 'etc/localtime').readlink(), Path('/usr/share/zoneinfo/Asia/Seoul'))
            self.assertEqual((root / 'etc/timezone').read_text(), 'Asia/Seoul\n')

    def test_vm_units_do_not_wait_for_external_network_or_time_sync(self):
        directory = ROOT / 'platform/yocto/meta-xenova/recipes-core/xenova-platform/files'
        for name in ('xenova-vms.service', 'xenova-first-boot.service', 'xenova-host-admission.service'):
            text = (directory / name).read_text()
            self.assertNotRegex(text, r'(?:After|Requires|Wants)=.*(?:network-online|time-sync)')
