"""별도 /boot를 가진 공식 cloud 원본의 복사 회귀. mount/블록 장치는 대역만 사용한다."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from first_install_fixture import assemble
prepare = assemble.prepare


class SourceBootPartitionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'etc').mkdir(); (self.root / 'boot').mkdir()
        self.fstab = self.root / 'etc/fstab'
        self.fstab.write_text('LABEL=cloudimg-rootfs / ext4 defaults 0 1\nLABEL=BOOT /boot ext4 defaults 0 2\nLABEL=UEFI /boot/efi vfat defaults 0 1\n')
        self.parts = [Path('/dev/loop71p1'), Path('/dev/loop71p15'), Path('/dev/loop71p16')]
        self.info = ['TYPE=ext4\nLABEL=cloudimg-rootfs\nUUID=root-id\n', 'TYPE=vfat\nLABEL=UEFI\n', 'TYPE=ext4\nLABEL=BOOT\nUUID=boot-id\nPART_ENTRY_UUID=boot-part-id\n']
        self.calls = []
        self.work = type('Work', (), {'mount': lambda _, device, target, readonly=False: self.calls.append((device,target,readonly))})()

    def call(self):
        with patch.object(prepare.subprocess, 'check_output', side_effect=self.info) as query:
            prepare.mount_source_boot(self.work, self.root, self.parts)
            return query

    def test_ubuntu_separate_boot_matches_only_owned_partitions_and_is_read_only(self):
        query=self.call()
        self.assertEqual(self.calls, [(self.parts[2],self.root/'boot',True)])
        self.assertEqual([c.args[0][-1] for c in query.call_args_list], [str(p) for p in self.parts])
        self.assertNotIn('efi',str(self.calls))

    def test_single_root_layout_does_not_mount_or_probe_any_extra_partition(self):
        self.fstab.write_text('/dev/vda1 / ext4 defaults 0 1\n')
        with patch.object(prepare.subprocess, 'check_output', side_effect=AssertionError('unneeded probe')):
            prepare.mount_source_boot(self.work,self.root,self.parts)
        self.assertEqual(self.calls,[])

    def test_uuid_and_partuuid_references_supported(self):
        for reference in ('UUID=boot-id','PARTUUID=boot-part-id'):
            self.fstab.write_text(reference+' /boot ext4 defaults 0 2\n')
            self.call()
        self.assertEqual(len(self.calls),2)

    def test_missing_or_duplicated_boot_refused_without_guessing(self):
        for rows in ([self.info[0],self.info[1],self.info[0]],[self.info[2],self.info[1],self.info[2]]):
            with patch.object(prepare.subprocess,'check_output',side_effect=rows):
                with self.assertRaisesRegex(ValueError,'missing or duplicated'):
                    prepare.mount_source_boot(self.work,self.root,self.parts)
        self.assertEqual(self.calls,[])

    def test_external_device_and_unsupported_fstype_refused(self):
        for row in ('/dev/sda1 /boot ext4 defaults 0 2\n','LABEL=BOOT /boot vfat defaults 0 2\n'):
            self.fstab.write_text(row)
            with self.assertRaises(ValueError): prepare.mount_source_boot(self.work,self.root,self.parts)
        self.assertEqual(self.calls,[])

    def test_guest_boot_link_cannot_escape_root(self):
        (self.root/'boot').rmdir(); (self.root/'boot').symlink_to('../outside')
        with patch.object(prepare.subprocess,'check_output',side_effect=self.info):
            with self.assertRaisesRegex(ValueError,'escapes root'):
                prepare.mount_source_boot(self.work,self.root,self.parts)
        self.assertEqual(self.calls,[])
