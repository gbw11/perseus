"""계획·카드 용량·재개·고유화·실패 gate 모의 시험. 실제 이미지·SD·VM을 생성하지 않는다."""
import argparse
import copy
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from first_install_fixture import ROOT, FILES, Board, assemble, boot, contract, guest_identity, mbr, prepare_rootfs, vm, platform_root
import test_xenova_platform as contracts
from test_xenova_platform import record, fixture


class CapacityTest(unittest.TestCase):
    def guests(self):
        return [{'logical_bytes': 8 * contract.GIB, 'allocated_bytes': contract.GIB,
                 'boot_bytes': 128 * contract.MIB, 'preparation_scratch_bytes': 4 * contract.GIB} for _ in range(2)]

    def test_64_and_128gb_actual_bytes_fit_full_templates_without_overcommit(self):
        for size in (63_864_569_856, 127_865_454_592):
            plan = contract.capacity(3347054592, self.guests(), card_bytes=size)
            self.assertLess(plan['image_bytes'], size)
            self.assertEqual(plan['retained_template_root_bytes'], 16 * contract.GIB)
            self.assertEqual(plan['vm_max_bytes'], 16 * contract.GIB)
            self.assertEqual(plan['vm_growth_budget_bytes'], 14 * contract.GIB)
            self.assertGreater(plan['wsl_scratch_upper_bound_bytes'], 2 * plan['image_bytes'])

    def test_32gb_and_insufficient_capacity_fail_without_silent_template_omission(self):
        for size in (32_000_000_000, 1024):
            with self.assertRaisesRegex(ValueError, 'insufficient card'):
                contract.capacity(3347054592, self.guests(), card_bytes=size)
        with self.assertRaises(ValueError):
            contract.capacity(3347054592, self.guests(), template_policy='catalog-only')

    def test_sparse_accounting_never_reduces_logical_commitment(self):
        guests = self.guests()
        a = contract.capacity(3347054592, guests)
        for g in guests:
            g['allocated_bytes'] = 0
        b = contract.capacity(3347054592, guests)
        self.assertEqual(a['minimum_card_bytes'], b['minimum_card_bytes'])
        self.assertGreater(b['vm_growth_budget_bytes'], a['vm_growth_budget_bytes'])

    def test_invalid_negative_or_nonsensical_disk_accounting_rejected(self):
        for value in (-1, 33 * contract.GIB):
            guests = self.guests()
            guests[0]['allocated_bytes'] = value
            with self.assertRaises(ValueError):
                contract.capacity(3347054592, guests)


class InputSafetyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / 'input'
        self.source.write_bytes(b'fixture')

    def test_input_hash_mismatch_and_symlink_rejected(self):
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            assemble.checked(self.source, '0' * 64)
        linked = self.base / 'latest'
        linked.symlink_to(self.source)
        with self.assertRaisesRegex(ValueError, 'latest symlink'):
            assemble.checked(linked, hashlib.sha256(b'fixture').hexdigest())

    def test_existing_output_or_device_output_refused(self):
        for path in (self.source, Path('/dev/xenova-output'), Path('/proc/xenova-output')):
            with self.assertRaises(ValueError):
                assemble.new_path(path)

    def test_publication_does_not_replace_racing_existing_output(self):
        stage, destination = self.base / 'stage', self.base / 'output'
        stage.mkdir()
        destination.mkdir()
        (destination / 'keep').write_text('user data')
        with self.assertRaises(ValueError):
            assemble.publish(stage, destination)
        self.assertEqual((destination / 'keep').read_text(), 'user data')

    def test_direct_build_input_requires_publication_without_creating_a_lock(self):
        build = self.base / 'build'
        build.mkdir()
        config = {'platform': {'image': str(self.source)}}
        with self.assertRaisesRegex(ValueError, 'published completed platform required'):
            with assemble.completed_input(config):
                self.fail('accepted live build output')
        self.assertEqual(list(build.iterdir()), [])

    def test_old_platform_without_first_boot_is_rejected_without_modification(self):
        root = self.base / 'root'
        root.mkdir()
        with self.assertRaisesRegex(ValueError, 'rebuild dom0'):
            assemble.platform_root_ok(root)
        self.assertEqual(list(root.iterdir()), [])

    def test_malicious_rootfs_link_cannot_modify_host_path(self):
        root = self.base / 'root'
        (root / 'usr/share').mkdir(parents=True)
        sentinel = self.base / 'outside'
        sentinel.mkdir()
        (sentinel / 'keep').write_text('preserve')
        (root / 'usr/share/xenova').symlink_to('../../../../outside')
        with self.assertRaises(ValueError):
            assemble.platform_root_ok(root)
        self.assertEqual((sentinel / 'keep').read_text(), 'preserve')

    def test_guest_symlink_into_mounted_special_filesystem_is_refused(self):
        root = self.base / 'root'
        (root / 'etc').mkdir(parents=True)
        (root / 'etc/modules').symlink_to('/proc/sys/kernel/hostname')
        with self.assertRaisesRegex(ValueError,'special filesystem'):
            prepare_rootfs.inside(root,'etc/modules')

    def test_plan_does_not_call_assembler_even_when_execute_is_present(self):
        from contextlib import contextmanager
        cfg = self.base / 'config.json'
        cfg.write_text('{}')
        @contextmanager
        def completed(_):
            yield self.source, {}
        with patch.object(assemble, 'completed_input', completed), patch.object(assemble, 'plan', return_value={'action': 'plan'}), patch.object(assemble, 'assemble', side_effect=AssertionError('must not execute')):
            assemble.main(['plan', '--execute', '--config', str(cfg), '--output-dir', str(self.base / 'out'), '--work-dir', str(self.base / 'work'), '--card-bytes', '64000000000'])

    def test_claimed_capability_requires_real_modules_units_and_tools(self):
        root = platform_root(self.base / 'root')
        result = assemble.platform_root_ok(root)
        self.assertIn('usr/lib/xenova/xenova_first_boot.py', result)
        (root / 'usr/bin/resize2fs').unlink()
        with self.assertRaisesRegex(ValueError, 'growth tool missing'):
            assemble.platform_root_ok(root)

    def test_assembler_rejects_old_root_before_guest_preparation_or_publication(self):
        root = self.base / 'old-root'
        root.mkdir()
        output, work = self.base / 'out', self.base / 'scratch'
        planned = {'output_directory': str(output), 'work_directory': str(work),
                   'capacity': {'wsl_scratch_upper_bound_bytes': 0}, 'layout': {}}
        with patch.object(assemble.os, 'geteuid', return_value=0), patch.object(assemble.shutil, 'which', return_value='/fixture/tool'), patch.object(assemble, 'platform_mount', return_value=root), patch.object(assemble, 'platform_boot_ok', return_value={}), patch.object(assemble.prepare, 'make_instance') as create, patch.object(assemble.prepare, 'make_template') as template:
            with self.assertRaisesRegex(ValueError, 'rebuild dom0'):
                assemble.assemble({}, self.source, {}, planned)
        create.assert_not_called()
        template.assert_not_called()
        self.assertFalse(output.exists())
        self.assertEqual(self.source.read_bytes(), b'fixture')
        self.assertEqual(list(root.iterdir()), [])

    def test_atomic_publication_checks_noreplace_even_after_precheck_race(self):
        stage, target = self.base / 'stage', self.base / 'target'
        stage.mkdir(); target.mkdir()
        original_inode = target.stat().st_ino
        with patch('artifact_io.new_path'):
            with self.assertRaises(OSError):
                assemble.publish(stage, target)
        self.assertEqual(target.stat().st_ino, original_inode)
        self.assertTrue(stage.is_dir())

    def test_result_verifier_rejects_changed_file_before_layout_check(self):
        with self.assertRaisesRegex(ValueError, 'hash/size'):
            assemble.verify(self.source, {'capacity': {'image_bytes': 7}, 'image_sha256': '0' * 64})


class AssemblyPipelineTest(unittest.TestCase):
    def test_emulated_pipeline_preserves_input_partitions_and_publishes_all_outputs(self):
        """8KiB의 비 OS fixture만 조립하며 mkfs/mount/게스트 준비는 전부 명령 대역이다."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        image = base / 'synthetic-platform.bin'
        content = bytearray(2048)
        struct.pack_into('<I', content, 440, 0x1234ABCD)
        for index, (start, count, kind) in enumerate([(1,1,12),(2,2,131)]):
            content[446+index*16:462+index*16] = struct.pack('<B3sB3sII', 0, b'\xfe\xff\xff', kind, b'\xfe\xff\xff', start, count)
        content[510:512] = b'\x55\xaa'
        content[512:1024] = b'BOOT-FIXTURE'.ljust(512, b'.')
        content[1024:] = b'ROOT-FIXTURE'.ljust(1024, b'.')
        image.write_bytes(content)
        prepared_root = platform_root(base / 'platform-root')
        board = Board(base / 'template-source')
        board.factory()
        defaults = json.loads((FILES / 'defaults.json').read_text())['instances']
        guests = [{'id': r['id'], 'source_id': r['template'], 'template': str(board.data / 'templates' / r['template'])} for r in defaults]
        from test_xenova_zephyr import bundle
        firmware = bundle(base / 'zephyr-template')
        firmware_entry = assemble.rtos.info(firmware, assemble.sha(firmware / 'template.json'))
        workdir, output = base / 'scratch', base / 'release'
        planned = {'rtos_templates': [firmware_entry], 'guests': guests, 'output_directory': str(output), 'work_directory': str(workdir),
                   'layout': contract.layout(content[:512], len(content)), 'features_required': contract.FEATURES,
                   'capacity': {'wsl_scratch_upper_bound_bytes': 0, 'data_bytes': 4096, 'image_bytes': 8192,
                                'data_start_bytes': 4096, 'template_policy': 'full', 'operating_reserve_bytes': 0}}
        class Work:
            def __init__(self, parent):
                self.path = parent / 'emulated-workspace'
                self.path.mkdir()
                self.mounts, self.loops = [], []
            def loop(self, *_args, **_kwargs):
                self.loops.append('owned-emulated-loop')
                return self.loops[-1]
            def mount(self, _device, target, **_kwargs):
                target.mkdir()
                self.mounts.append(target)
            def close(self):
                self.mounts.clear(); self.loops.clear()
        def init(data, **_kwargs):
            for name in ('templates','instances','seeds'):
                (data / name).mkdir()
        def seed(args):
            source = board.data / 'seeds' / args.id
            shutil.copytree(source, args.data_root / 'seeds' / args.id)
        def command(args, **_kwargs):
            if args[0] == 'cp':
                shutil.copyfile(args[-2],args[-1])
            elif args[0] != 'mkfs.ext4':
                raise AssertionError('unexpected command: ' + repr(args))
        # 소스 스냅샷에도 Git 작업 트리는 없을 수 있다. 출처 조회도 외부 경계다.
        def git_output(args, **_kwargs):
            self.assertEqual(args[:3], ['git', '-C', str(ROOT)])
            if args[3:] == ['rev-parse', 'HEAD']:
                return 'a' * 40 + '\n'
            if args[3:] == ['status', '--porcelain']:
                return ' M fixture.py\n'
            raise AssertionError('unexpected Git query: ' + repr(args))
        config = {'platform': {'sha256': assemble.sha(image)}}
        with patch.object(assemble.subprocess, 'check_output', side_effect=git_output) as git_query, patch.object(assemble.os,'geteuid',return_value=0), patch.object(assemble.shutil,'which',return_value='/fixture/tool'), patch.object(assemble.prepare,'Workspace',Work), patch.object(assemble,'platform_mount',return_value=prepared_root), patch.object(assemble,'platform_boot_ok',return_value={'xen':'fixture-hash'}), patch.object(assemble.prepare,'initialize_storage',side_effect=init), patch.object(assemble.prepare,'make_instance',side_effect=seed), patch.object(assemble.prepare,'command',side_effect=command), patch.object(assemble,'template_info',return_value={}), patch.object(assemble,'copy_management_catalog'), patch.object(assemble.os,'sync'):
            manifest = assemble.assemble(config,image,{},planned)
        self.assertEqual(git_query.call_count, 2)
        self.assertEqual(manifest['assembly_source_commit'], 'a' * 40)
        self.assertTrue(manifest['assembly_source_dirty'])
        self.assertEqual(image.read_bytes(),content)
        self.assertEqual({p.name for p in output.iterdir()}, {'xenova-first-install.img','xenova-first-install.manifest.json','SHA256SUMS'})
        final = output / 'xenova-first-install.img'
        data = final.read_bytes()
        self.assertEqual(data[:478],content[:478])
        self.assertEqual(data[494:2048],content[494:2048])
        self.assertTrue(assemble.verify(final,manifest)['verified'])
        self.assertEqual(manifest['layout']['disk_id'],'1234abcd')
        self.assertEqual(manifest['factory']['firmware_instances'][0]['record']['id'], 'zephyr-dev')
        self.assertFalse(manifest['factory']['firmware_instances'][0]['record']['autostart'])
        self.assertEqual([t['retained_root'] for t in manifest['factory']['templates']], [True,True])
        with final.open('r+b') as stream:
            stream.seek(1024); stream.write(b'corruption')
        with self.assertRaisesRegex(ValueError,'hash/size'):
            assemble.verify(final,manifest)


class GrowthTest(unittest.TestCase):
    def board(self, size=64_000_000_000):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return Board(Path(temp.name), size)

    def test_64_and_128gb_grow_only_last_partition_preserving_mbr_identity(self):
        for size in (64_000_000_000, 128_000_000_000):
            board = self.board(size)
            before = board.disk.read_bytes()
            board.p.grow()
            after = board.disk.read_bytes()
            self.assertEqual(before[:478], after[:478])
            self.assertEqual(before[494:], after[494:])
            self.assertEqual(board.table_bytes(), size - 6686720 * 512)
            self.assertEqual(board.fs_size, board.kernel_bytes // 4096 * 4096)
            self.assertFalse(any('mkfs' in call[0][0] for call in board.calls))
            args = next(c[0] for c in board.calls if c[0][0] == 'sfdisk')
            self.assertEqual(args[args.index('--wipe')+1], 'never')
            self.assertEqual(args[args.index('--wipe-partitions')+1], 'never')

    def test_already_extended_is_noop_for_partition_and_filesystem(self):
        b = self.board()
        b.p.grow()
        b.calls.clear()
        b.p.grow()
        self.assertFalse(any(c[0][0] in ('sfdisk', 'partx', 'e2fsck', 'resize2fs') for c in b.calls))

    def test_partition_write_interruption_resumes_without_moving_start(self):
        b = self.board()
        b.failure = 'after-partition'
        with self.assertRaisesRegex(ValueError, 'interruption'):
            b.p.grow()
        start = contract.layout(b.disk.read_bytes(), with_data=True)['partitions'][2]['start']
        b.failure = None
        b.p.grow()
        self.assertEqual(start, 6686720)
        self.assertEqual(b.fs_size, b.kernel_bytes // 4096 * 4096)

    def test_resize_interruption_retries_without_second_resize(self):
        b = self.board()
        b.failure = 'after-resize'
        with self.assertRaisesRegex(ValueError, 'interruption'):
            b.p.grow()
        b.failure = None
        b.calls.clear()
        b.p.grow()
        self.assertFalse(any(c[0][0] == 'resize2fs' for c in b.calls))

    def test_kernel_update_and_fsck_failures_do_not_publish_mount_path(self):
        for failure in ('partx', 'fsck'):
            b = self.board()
            b.failure = failure
            with self.assertRaises(ValueError):
                b.p.grow()
            self.assertFalse((b.root / 'run/xenova/data-device').exists())

    def test_insufficient_card_mounted_data_and_root_reference_mismatch_refused(self):
        b = self.board(5 * contract.GIB)
        with self.assertRaises(ValueError):
            b.p.grow()
        b = self.board()
        b.set_mount(True)
        with self.assertRaisesRegex(ValueError, 'already mounted'):
            b.p.grow()
        b = self.board()
        (b.root / 'proc/cmdline').write_text('root=PARTUUID=99999999-02')
        with self.assertRaisesRegex(ValueError, 'PARTUUID'):
            b.p.grow()

    def test_duplicate_boot_partuuid_is_refused_before_growth(self):
        b = self.board()
        node = b.root / 'sys/devices/other-disk/sdy2'
        node.mkdir(parents=True)
        (node / 'partition').write_text('2')
        (b.root / 'sys/class/block/sdy2').symlink_to(node)
        before = b.disk.read_bytes()
        with self.assertRaisesRegex(ValueError, 'duplicate boot PARTUUID'):
            b.p.grow()
        self.assertEqual(b.disk.read_bytes(), before)
        self.assertFalse(any(c[0][0] in ('sfdisk','resize2fs') for c in b.calls))

    def test_partition_layout_rejects_gpt_extended_overlap_and_unexpected_fourth(self):
        for offset, value in ((450, 0xEE), (466, 5), (498, 131)):
            block = bytearray(mbr(100))
            block[offset] = value
            with self.assertRaises(ValueError):
                contract.layout(bytes(block), with_data=True)


class RegistrationTest(unittest.TestCase):
    def board(self, mid='1' * 32):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        b = Board(Path(temp.name), mid=mid)
        b.grow_mount()
        b.factory()
        return b

    def test_first_install_and_rerun_preserve_disks_identity_and_user_autostart_choice(self):
        b = self.board()
        b.p.register()
        directory = b.data / 'instances/ubuntu-dev'
        before = json.loads((directory / 'instance.json').read_text())
        before['autostart'] = False
        (directory / 'instance.json').write_text(json.dumps(before))
        disk = directory / 'root.raw'
        disk.write_bytes(b'USER CHANGED'.ljust(disk.stat().st_size, b'.'))
        content = disk.read_bytes()
        state = (b.data / '.first-install-state.json').read_bytes()
        b.p.register()
        self.assertEqual(json.loads((directory / 'instance.json').read_text()), before)
        self.assertEqual(disk.read_bytes(), content)
        self.assertEqual((b.data / '.first-install-state.json').read_bytes(), state)

    def test_same_common_seed_on_two_cards_gets_distinct_vm_and_storage_identities(self):
        a, b = self.board('1' * 32), self.board('2' * 32)
        a.p.register(); b.p.register()
        for key in ('ubuntu-dev', 'debian-dev'):
            x = json.loads((a.data / 'instances' / key / 'instance.json').read_text())
            y = json.loads((b.data / 'instances' / key / 'instance.json').read_text())
            self.assertNotEqual(x['uuid'], y['uuid'])
            self.assertNotEqual(x['mac'], y['mac'])
            self.assertEqual(x['ipv4'], y['ipv4'])
            self.assertEqual(x['name'], y['name'])
        self.assertNotEqual((a.data / '.xenova-storage.json').read_bytes(), (b.data / '.xenova-storage.json').read_bytes())

    def test_interruption_after_identity_save_resumes_same_planned_uuid(self):
        b = self.board()
        with patch.object(boot.os, 'rename', side_effect=OSError('injected before rename')):
            with self.assertRaises(OSError):
                b.p.register()
        planned = json.loads((b.data / '.first-install-state.json').read_text())['identities']
        b.p.register()
        r = json.loads((b.data / 'instances/ubuntu-dev/instance.json').read_text())
        self.assertEqual(r['uuid'], planned['ubuntu-dev']['uuid'])
        self.assertEqual(r['mac'], planned['ubuntu-dev']['mac'])

    def test_interruption_after_rename_resumes_without_new_disk_or_identity(self):
        b = self.board()
        original = b.p.save
        def interrupted(path, value):
            if path.name == '.first-install-state.json' and value['registered']:
                raise OSError('injected after rename')
            original(path, value)
        with patch.object(b.p, 'save', side_effect=interrupted):
            with self.assertRaises(OSError):
                b.p.register()
        inode = (b.data / 'instances/ubuntu-dev/root.raw').stat().st_ino
        b.p.register()
        self.assertEqual(inode, (b.data / 'instances/ubuntu-dev/root.raw').stat().st_ino)

    def test_missing_or_corrupt_asset_prevents_completion_and_vm_gate(self):
        for name in ('root.raw', 'Image'):
            b = self.board()
            (b.data / 'seeds/ubuntu-dev' / name).unlink()
            with self.assertRaises(ValueError):
                b.p.register()
            state = json.loads((b.data / '.first-install-state.json').read_text())
            self.assertNotEqual(state['phase'], 'complete')
            self.assertFalse((b.root / 'run/xenova/install-ready.json').exists())

    def test_completed_missing_instance_is_never_recreated(self):
        b = self.board()
        b.p.register()
        shutil.rmtree(b.data / 'instances/ubuntu-dev')
        with self.assertRaisesRegex(ValueError, 'never recreated'):
            b.p.register()
        self.assertFalse((b.data / 'instances/ubuntu-dev').exists())

    def test_existing_data_without_progress_and_changed_dom0_id_are_refused(self):
        b = self.board()
        (b.data / '.xenova-storage.json').write_text('{"user":"keep"}')
        with self.assertRaisesRegex(ValueError, 'cannot be adopted'):
            b.p.register()
        self.assertEqual((b.data / '.xenova-storage.json').read_text(), '{"user":"keep"}')
        b = self.board()
        b.p.register()
        (b.root / 'etc/machine-id').write_text('2' * 32 + '\n')
        with self.assertRaisesRegex(ValueError, 'another install'):
            b.p.register()

    def test_incomplete_install_cannot_pass_runtime_storage_gate(self):
        b = self.board()
        (b.data / '.first-install-state.json').write_text('{"schema":1,"phase":"registering"}')
        mountinfo = b.root / 'mountinfo-for-vm'
        mountinfo.write_text(f'2 1 8:3 / {b.data} rw - ext4 /dev/sdz3 rw\n')
        with self.assertRaisesRegex(vm.PlatformError, 'incomplete'):
            vm.storage_check(b.data, mountinfo)


class GuestIdentityTest(unittest.TestCase):
    def root(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        contracts.GuestPreparationTest().root(root)
        (root / 'usr/bin').mkdir(parents=True, exist_ok=True)
        (root / 'usr/bin/python3').write_text('fixture interpreter, never executed')
        prepare_rootfs.prepare_template(root)
        prepare_rootfs.prepare_deployment_seed(root, record())
        return root

    def generator(self, root, calls, interrupt=False):
        def command(args, **kwargs):
            if '-A' not in args:
                return SimpleNamespace(returncode=0, stdout='mock validated public key')
            calls.append(args)
            for index, kind in enumerate(('rsa', 'ecdsa', 'ed25519')):
                key = root / f'etc/ssh/ssh_host_{kind}_key'
                if not key.exists():
                    key.write_text('mock key: ' + kind)
                if interrupt and index == 0:
                    raise OSError('mock interrupted key generation')
        return command

    def test_seed_keeps_mid_uninitialized_and_has_no_shared_keys(self):
        root = self.root()
        self.assertEqual((root / 'etc/machine-id').read_text(), 'uninitialized\n')
        self.assertFalse(list((root / 'etc/ssh').glob('ssh_host_*_key')))
        self.assertIn('Requires=xenova-guest-identity.service', (root / 'etc/systemd/system/ssh.service.d/10-xenova-identity.conf').read_text())

    def test_first_guest_ssh_preparation_and_rerun_preserve_changed_password_and_keys(self):
        root = self.root()
        (root / 'etc/machine-id').write_text('a' * 32 + '\n')  # PID1을 모의한다. 실행 중인 OS의 ID가 아니다.
        (root / 'etc/shadow').write_text('root:USER_CHANGED_PASSWORD:1:0:99999:7:::\n')
        calls = []
        guest_identity.ensure(root, self.generator(root, calls))
        before = (root / 'etc/xenova/identity-complete.json').read_bytes()
        guest_identity.ensure(root, self.generator(root, calls))
        self.assertEqual(len(calls), 1)
        self.assertEqual((root / 'etc/xenova/identity-complete.json').read_bytes(), before)
        self.assertIn('USER_CHANGED_PASSWORD', (root / 'etc/shadow').read_text())

    def test_interrupted_guest_key_setup_retains_existing_keys_and_resumes(self):
        root = self.root()
        (root / 'etc/machine-id').write_text('b' * 32)
        calls = []
        with self.assertRaises(OSError):
            guest_identity.ensure(root, self.generator(root, calls, interrupt=True))
        before = (root / 'etc/ssh/ssh_host_rsa_key').read_bytes()
        guest_identity.ensure(root, self.generator(root, calls))
        self.assertEqual((root / 'etc/ssh/ssh_host_rsa_key').read_bytes(), before)

    def test_completed_guest_with_missing_key_fails_without_regeneration(self):
        root = self.root()
        (root / 'etc/machine-id').write_text('c' * 32)
        calls = []
        guest_identity.ensure(root, self.generator(root, calls))
        (root / 'etc/ssh/ssh_host_rsa_key').unlink()
        with self.assertRaisesRegex(ValueError, 'no automatic'):
            guest_identity.ensure(root, self.generator(root, calls))
        self.assertEqual(len(calls), 1)

    def test_corrupt_generated_key_prevents_guest_identity_completion(self):
        root = self.root()
        (root / 'etc/machine-id').write_text('d' * 32)
        calls = []
        generate = self.generator(root,calls)
        def command(args, **kwargs):
            if '-y' in args:
                raise subprocess.CalledProcessError(1,args)
            return generate(args,**kwargs)
        with self.assertRaises(subprocess.CalledProcessError):
            guest_identity.ensure(root,command)
        self.assertFalse((root / 'etc/xenova/identity-complete.json').exists())

    def test_uninitialized_mid_does_not_start_ssh_or_generate_keys(self):
        root = self.root()
        with self.assertRaisesRegex(ValueError, 'machine-id'):
            guest_identity.ensure(root, lambda *a, **k: self.fail('must not generate keys'))


class NativeCreateTest(unittest.TestCase):
    def scenario(self):
        from test_xenova_management import ManagementTest
        case=ManagementTest()
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def arguments(self,case,execute):
        return argparse.Namespace(id='custom',vm_id=case.spec['vm_id'],template=case.spec['template_id'],template_version='1.0.0',
                                  ipv4=case.spec['ipv4'],memory_mib=512,vcpus=1,execute=execute,idempotency_key='facade')

    def test_missing_local_template_and_capacity_failure_do_not_create(self):
        case=self.scenario();args=self.arguments(case,True);args.template='missing'
        result=case.p.create(args,{})
        self.assertEqual(result['error']['code'],'TEMPLATE_NOT_FOUND')
        self.assertEqual(list((case.data/'instances').iterdir()),[])
        self.assertFalse((case.data/'operations').exists())

    def test_native_plan_uses_local_template_and_never_creates_or_starts(self):
        case=self.scenario()
        import prepare_instance as backend
        with patch.object(backend,'make_instance') as create,patch.object(case.p,'start') as start:
            result=case.p.create(self.arguments(case,False),{})
        self.assertTrue(result['ok'],result);create.assert_not_called();start.assert_not_called()
        self.assertFalse((case.data/'operations').exists())

    def test_native_create_reserves_existing_sparse_growth_before_copy(self):
        case=self.scenario();existing=record();directory=fixture(case.data,existing)
        original=Path.stat
        def diskstat(path,*args,**kwargs):
            st=original(path,*args,**kwargs)
            if path==directory/'root.raw':
                return SimpleNamespace(st_mode=st.st_mode,st_nlink=st.st_nlink,st_dev=st.st_dev,
                                       st_ino=st.st_ino,st_size=8*contract.GIB,st_blocks=0)
            return st
        with patch.object(vm.os,'statvfs',return_value=SimpleNamespace(f_bavail=9*contract.GIB,f_frsize=1)),patch.object(Path,'stat',diskstat):
            result=case.p.create(self.arguments(case,True),{})
        self.assertEqual(result['error']['code'],'INSUFFICIENT_STORAGE',result)
        self.assertFalse((case.data/('instances/'+case.key)).exists())

    def test_native_execute_delegates_once_to_shared_creator_without_starting_guest(self):
        case=self.scenario()
        import prepare_instance as backend
        def create(args,**kwargs):
            r=kwargs['record_override']
            fixture(case.data,r)
        with patch.object(backend,'make_instance',side_effect=create) as creator,patch.object(case.p,'start') as start:
            result=case.p.create(self.arguments(case,True),{})
        self.assertTrue(result['ok'],result);creator.assert_called_once();start.assert_not_called()
        record_override=creator.call_args.kwargs['record_override']
        self.assertEqual(record_override['template'],'source')
        self.assertFalse(record_override['autostart'])
        self.assertEqual(record_override['uuid'],result['data']['vm_id'])

    def test_shared_creator_refuses_linked_output_parent_before_storage_or_copy(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        data, outside = base / 'data', base / 'outside'
        data.mkdir(); outside.mkdir()
        (data / 'templates').mkdir()
        (data / 'instances').symlink_to(outside)
        import prepare_instance as backend
        args = argparse.Namespace(data_root=data,id='new-vm',deployment_seed=False)
        with self.assertRaisesRegex(ValueError,'canonical and unlinked'):
            backend.make_instance(args,vm=vm,defaults_path=FILES / 'defaults.json')
        self.assertEqual(list(outside.iterdir()),[])

    def test_existing_id_is_not_reinitialized(self):
        case=self.scenario()
        case.create()
        before=(case.data/f'instances/{case.key}/instance.json').read_bytes()
        result=case.p.create(self.arguments(case,True),{})
        self.assertTrue(result['ok'],result)
        self.assertTrue(result['replayed'],result)
        self.assertEqual(case.copies,1)
        self.assertEqual((case.data/f'instances/{case.key}/instance.json').read_bytes(),before)


if __name__ == '__main__':
    unittest.main()
