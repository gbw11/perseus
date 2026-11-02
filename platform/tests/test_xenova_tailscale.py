"""검증된 mount/device 경계만 대체하고 실제 상태 보존·같은 보드 복원 코드를 시험한다."""
import base64
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
LAYER=ROOT/'platform/yocto/meta-xenova'
FILES=LAYER/'recipes-connectivity/tailscale/files'
loader=importlib.machinery.SourceFileLoader('xenova_tailscale_state',str(FILES/'xenova-tailscale-state'))
spec=importlib.util.spec_from_loader(loader.name,loader)
state=importlib.util.module_from_spec(spec);loader.exec_module(state)


class OwnedMetadata:
    def __init__(self, value): self.value=value
    def __getattr__(self, name):
        return 0 if name=='st_uid' else getattr(self.value,name)


class StateTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='xenova-tailscale-unit-')
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.data=self.base/'data';self.data.mkdir()
        self.serial=self.base/'serial';self.serial.write_bytes(b'1000000012345678\x00')
        self.target=self.data/'tailscale';self.target.mkdir(mode=0o700)
        self.content=b'{"_machinekey":"synthetic-unit-fixture-only"}\n'
        (self.target/'tailscaled.state').write_bytes(self.content)
        real_lstat=Path.lstat
        self.owner=patch.object(Path,'lstat',lambda p,*a,**k:OwnedMetadata(real_lstat(p,*a,**k)))
        self.owner.start();self.addCleanup(self.owner.stop)
        self.mounted=patch.object(state,'storage',lambda data=state.DATA,**kw: data/'tailscale')
        self.mounted.start();self.addCleanup(self.mounted.stop)
        self.guards=[]

    def guard(self): self.guards.append('stopped')
    def export(self):
        return state.backup(data=self.data,serial_path=self.serial,guard=self.guard)
    def encoded(self): return json.dumps(self.export()).encode()
    def fresh(self):
        self.target.rename(self.base/'preserved-old-state')

    def test_prepare_preserves_existing_state_and_tightens_permissions(self):
        os.chmod(self.target,0o755);os.chmod(self.target/'tailscaled.state',0o644)
        state.prepare(self.data)
        self.assertEqual((self.target/'tailscaled.state').read_bytes(),self.content)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode),0o700)
        self.assertEqual(stat.S_IMODE((self.target/'tailscaled.state').stat().st_mode),0o600)

    def test_first_install_creates_only_a_private_directory(self):
        self.fresh();state.prepare(self.data)
        self.assertEqual(list(self.target.iterdir()),[])
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode),0o700)

    def test_post_stop_cleanup_keeps_the_same_state_path_and_never_falls_back(self):
        with patch.object(state.subprocess,'run') as command:
            state.cleanup(data=self.data)
            argv=command.call_args.args[0]
            self.assertIn('--statedir='+str(self.target),argv)
            self.assertIn('--state='+str(self.target/'tailscaled.state'),argv)
            self.assertEqual(argv[-1],'--cleanup')
        with patch.object(state,'storage',side_effect=state.StateError('mount absent')),patch.object(state.subprocess,'run') as command:
            with self.assertRaises(state.StateError):state.cleanup(data=self.data)
            command.assert_not_called()

    def test_same_board_round_trip_to_fresh_data_preserves_all_files(self):
        (self.target/'profile').mkdir();(self.target/'profile'/'settings').write_bytes(b'opaque-settings')
        raw=self.encoded();self.fresh()
        result=state.restore(raw,data=self.data,serial_path=self.serial,guard=self.guard)
        self.assertTrue(result['restored']);self.assertTrue(result['same_physical_board'])
        self.assertEqual((self.target/'tailscaled.state').read_bytes(),self.content)
        self.assertEqual((self.target/'profile'/'settings').read_bytes(),b'opaque-settings')
        self.assertEqual(stat.S_IMODE((self.target/'tailscaled.state').stat().st_mode),0o600)
        self.assertEqual(len(self.guards),4)

    def test_check_mode_has_no_writes_and_allows_explicit_existing_target_review(self):
        raw=self.encoded();before=(self.target/'tailscaled.state').stat().st_mtime_ns
        value=state.restore(raw,check=True,data=self.data,serial_path=self.serial,guard=self.guard)
        self.assertTrue(value['target_exists']);self.assertNotIn('restored',value)
        self.assertEqual((self.target/'tailscaled.state').stat().st_mtime_ns,before)

    def test_different_physical_board_refused_without_creating_state(self):
        raw=self.encoded();self.fresh();self.serial.write_text('1000000087654321')
        with self.assertRaisesRegex(state.StateError,'different physical board'):
            state.restore(raw,data=self.data,serial_path=self.serial,guard=self.guard)
        self.assertFalse(self.target.exists());self.assertEqual(list(self.data.iterdir()),[])

    def test_missing_hardware_identity_never_falls_back_to_vm_or_host_id(self):
        for raw in (b'',b'0000000000000000',b'hostname-is-not-a-board-serial'):
            self.serial.write_bytes(raw)
            with self.assertRaises(ValueError):self.export()

    def test_existing_state_is_never_overwritten(self):
        raw=self.encoded()
        with self.assertRaisesRegex(state.StateError,'already exists'):
            state.restore(raw,data=self.data,serial_path=self.serial,guard=self.guard)
        self.assertEqual((self.target/'tailscaled.state').read_bytes(),self.content)

    def test_checksum_failure_is_rejected_before_publish(self):
        bundle=self.export();bundle['files'][0]['content']=base64.b64encode(b'corrupted').decode();self.fresh()
        with self.assertRaisesRegex(state.StateError,'checksum'):
            state.restore(json.dumps(bundle).encode(),data=self.data,serial_path=self.serial,guard=self.guard)
        self.assertEqual(list(self.data.iterdir()),[])

    def test_path_traversal_duplicates_and_file_directory_conflicts_are_rejected(self):
        original=self.export()
        for name in ('../outside','/absolute','a/../outside','a//b','.','a\\b','a\nb'):
            with self.subTest(name=name):
                bundle=json.loads(json.dumps(original));bundle['files'][0]['path']=name
                with self.assertRaises(ValueError):state.decode_backup(json.dumps(bundle),self.serial)
        for name in ('tailscaled.state','tailscaled.state/nested'):
            bundle=json.loads(json.dumps(original));item=dict(bundle['files'][0]);item['path']=name;bundle['files'].append(item)
            with self.assertRaises(ValueError):state.decode_backup(json.dumps(bundle),self.serial)

    def test_duplicate_json_keys_and_invalid_base64_are_rejected(self):
        with self.assertRaisesRegex(state.StateError,'duplicate'):
            state.decode_backup(b'{"format":1,"format":2}',self.serial)
        bundle=self.export();bundle['files'][0]['content']='@not-base64'
        with self.assertRaises(ValueError):state.decode_backup(json.dumps(bundle),self.serial)

    def test_links_and_special_files_are_not_exported(self):
        (self.target/'link').symlink_to(self.target/'tailscaled.state')
        with self.assertRaisesRegex(state.StateError,'link, special'):
            self.export()
        (self.target/'link').unlink()
        os.link(self.target/'tailscaled.state',self.target/'hard-link')
        with self.assertRaisesRegex(state.StateError,'link, special'):
            self.export()

    def test_non_stopped_daemon_and_resumed_start_before_publish_block_restore(self):
        raw=self.encoded();self.fresh()
        def busy():raise state.StateError('daemon active')
        with self.assertRaisesRegex(state.StateError,'daemon active'):
            state.restore(raw,data=self.data,serial_path=self.serial,guard=busy)
        calls=[]
        def race():
            calls.append(1)
            if len(calls)>1:raise state.StateError('daemon active')
        with self.assertRaisesRegex(state.StateError,'daemon active'):
            state.restore(raw,data=self.data,serial_path=self.serial,guard=race)
        self.assertFalse(self.target.exists());self.assertEqual(list(self.data.iterdir()),[])

    def test_storage_failure_during_restore_keeps_failure_and_no_published_state(self):
        raw=self.encoded();self.fresh()
        with patch.object(state.os,'fsync',side_effect=OSError('injected fsync failure')):
            with self.assertRaises(OSError):state.restore(raw,data=self.data,serial_path=self.serial,guard=self.guard)
        self.assertEqual(list(self.data.iterdir()),[])

    def test_interrupted_restore_blocks_fresh_identity_and_requires_operator_review(self):
        raw=self.encoded();self.fresh();pending=self.data/'.tailscale-restore-interrupted';pending.mkdir(mode=0o700)
        (pending/'tailscaled.state').write_bytes(b'partial')
        with self.assertRaisesRegex(state.StateError,'interrupted restore'):state.prepare(self.data)
        with self.assertRaisesRegex(state.StateError,'interrupted restore'):
            state.restore(raw,data=self.data,serial_path=self.serial,guard=self.guard)
        self.assertFalse(self.target.exists());self.assertEqual((pending/'tailscaled.state').read_bytes(),b'partial')

    def test_size_limit_does_not_silently_drop_state_files(self):
        with patch.object(state,'MAX_BYTES',4):
            with self.assertRaisesRegex(state.StateError,'bounded size'):self.export()


class MountAndMetadataTest(unittest.TestCase):
    def test_mount_failures_never_create_a_rootfs_state_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);data=base/'data';data.mkdir();device=base/'device';device.touch()
            table=base/'mountinfo';info=data.stat();dev=f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}'
            real_stat=Path.stat;real_lstat=Path.lstat
            class DeviceInfo:
                st_mode=stat.S_IFBLK|0o600;st_rdev=info.st_dev
            def observed(p,*a,**kw):return DeviceInfo() if p==device else real_stat(p,*a,**kw)
            for text in ('',f'1 0 {dev} / {data} rw - tmpfs tmpfs rw\n',
                         f'1 0 {dev} / {data} ro - ext4 /dev/owned ro\n'):
                table.write_text(text)
                with patch.object(Path,'stat',observed),patch.object(Path,'lstat',lambda p,*a,**kw:OwnedMetadata(real_lstat(p,*a,**kw))):
                    with self.assertRaises(state.StateError):state.prepare(data,device=device,mountinfo=table)
                self.assertFalse((data/'tailscale').exists())

    def test_pinned_recipe_and_service_remain_independent(self):
        append=(LAYER/'recipes-connectivity/tailscale/tailscale_%.bbappend').read_text()
        image=(LAYER/'recipes-core/images/xenova-xen-bringup-image.bb').read_text()
        unit=(FILES/'20-xenova-persistence.conf').read_text()
        self.assertIn('tailscale tailscaled kernel-module-tun',image)
        self.assertIn('SYSTEMD_AUTO_ENABLE:${PN}d = "enable"',append)
        self.assertIn('RequiresMountsFor=/srv/xenova',unit)
        self.assertIn('BindsTo=srv-xenova.mount',unit)
        self.assertIn('StateDirectory=\n',unit)
        self.assertIn('--state=/srv/xenova/tailscale/tailscaled.state',unit)
        self.assertNotIn('network-online.target',unit)
        for path in (LAYER/'recipes-core/xenova-platform/files').glob('*.service'):
            self.assertNotIn('tailscaled',path.read_text(),str(path))
        for option in ('--auth-key','--advertise-routes','--advertise-exit-node','--ssh','tailscale up'):
            self.assertNotIn(option,unit)

    def test_secure_static_fallback_is_version_architecture_and_hash_pinned(self):
        import re
        selected=re.search(r'PREFERRED_VERSION_tailscale = "([^"]+)"',(LAYER/'conf/layer.conf').read_text()).group(1)
        recipe=(LAYER/f'recipes-connectivity/tailscale/tailscale_{selected}.bb').read_text()
        self.assertGreaterEqual(tuple(map(int,selected.split('.'))),(1,104,2))
        self.assertIn('COMPATIBLE_HOST = "aarch64.*-linux"',recipe)
        self.assertIn('LICENSE = "BSD-3-Clause"',recipe)
        for name in ('archive','license'):
            self.assertRegex(recipe,r'SRC_URI\['+name+r'\.sha256sum\] = "[a-f0-9]{64}"')
        self.assertNotIn('tailscale-wait-online.service',recipe)
        self.assertNotIn('layers.lock',(LAYER/'conf/layer.conf').read_text())


if __name__=='__main__':unittest.main()
