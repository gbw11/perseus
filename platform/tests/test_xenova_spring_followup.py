"""factory 실제 신원과 공개 템플릿 버전 경계. 파일/등록/준비/게시 함수는 실제 실행한다."""
from argparse import Namespace
from contextlib import contextmanager, nullcontext
import copy
import hashlib
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from first_install_fixture import ROOT, Board, FILES
import test_xenova_management as fixtures
import prepare_instance as preparation
import xenova_management as management
import xenova_vm as vm


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SmallFilesWorkspace(preparation.Workspace):
    """loop·mount만 대역이다. 소유 workspace 생성과 파일 준비/게시는 실제다."""
    def loop(self, *args, **kwargs):
        return 'fixture-device'

    def mount(self, device, target, **kwargs):
        target.mkdir(parents=True, exist_ok=True)

    def close(self):
        pass


class FollowupFixture(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ManagementTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.p, self.m = self.f.p, self.f.m
        self.spec = {k:v for k,v in self.f.spec.items() if k != 'ipv4'}

    def error(self, result, code):
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['error']['code'], code, result)

    @contextmanager
    def linux_boundaries(self):
        """make_instance를 대체하지 않는다. OS/chroot/장치/전역 sync 경계만 대체한다."""
        calls = []
        def command(args, **kwargs):
            calls.append(args)
            if args[0] == 'cp':
                shutil.copyfile(args[-2], args[-1])
            elif args[0] != 'tune2fs':
                raise AssertionError('unexpected external OS command: ' + repr(args))
        original = self.m.preparer
        self.m.preparer = self.m._prepare
        try:
            with patch.object(preparation, 'Workspace', SmallFilesWorkspace), \
                 patch.object(preparation, 'command', command), \
                 patch.object(preparation, 'prepare_instance'), \
                 patch.object(preparation, 'verify_guest_ssh'), \
                 patch.object(preparation, 'guest_runtime', lambda *a, **k:nullcontext()), \
                 patch.object(preparation.os, 'sync'):
                yield calls
        finally:
            self.m.preparer = original


class FactoryReservationTest(FollowupFixture):
    def setUp(self):
        super().setUp()
        self.board = Board(self.f.data.parent/'board')
        self.board.grow_mount()
        self.board.factory()
        self.board.p.register()
        self.xen = fixtures.FakeXen(self.board.data, {})
        self.p = vm.Platform(self.board.data, self.board.root/'run/xenova',
                             command=self.xen, defaults_path=ROOT/'platform/tests/fixtures/legacy-defaults.json')
        network = patch.object(self.p, 'check_network')
        network.start()
        self.addCleanup(network.stop)
        self.m = management.Manager(self.p, usage_check=lambda *a:None, defaults_path=ROOT/'platform/tests/fixtures/legacy-defaults.json')
        self.records = self.p.records(validate_assets=False)
        defaults = json.loads((ROOT/'platform/tests/fixtures/legacy-defaults.json').read_text())['instances']
        for record in self.records.values():
            example = next(r for r in defaults if r['id'] == record['id'])
            self.assertNotEqual(record['mac'], example['mac'])
            with (self.board.data/'instances'/record['id']/'root.raw').open('r+b') as stream:
                stream.seek(1080)
                stream.write(b'\x53\xef')
        self.defaults_hash = sha(FILES/'defaults.json')
        self.identity_state = (self.board.data/'.first-install-state.json').read_bytes()

    def retire(self, record):
        result = self.m.call('retire', {'vm_id':record['uuid']}, execute=True,
                             idempotency_key='retire-'+record['id'])
        self.assertTrue(result['ok'], result)
        return self.m._archive(self.m._tombs()[record['uuid']])

    def restore(self, record, execute=True):
        return self.m.call('restore', {'vm_id':record['uuid']}, execute=execute,
                           idempotency_key='restore-'+record['id'])

    def test_actual_factory_mac_owns_reserved_ip_before_and_after_retire(self):
        for record in self.records.values():
            with self.subTest(name=record['id']):
                _, held = self.m._ip_inventory(self.p.records(validate_assets=False), excluding=record['uuid'])
                self.assertNotIn(record['ipv4'], held)
                self.retire(record)
                _, held = self.m._ip_inventory(self.p.records(validate_assets=False), excluding=record['uuid'])
                self.assertNotIn(record['ipv4'], held)

    def test_factory_restore_plan_recorded_retry_and_identity_preservation(self):
        for record in self.records.values():
            with self.subTest(name=record['id']):
                archive = self.retire(record)
                before = {p.name:p.read_bytes() for p in archive.iterdir()}
                inode = (archive/'root.raw').stat().st_ino
                self.assertTrue(self.restore(record, execute=False)['ok'])
                self.assertFalse((self.board.data/'instances'/record['id']).exists())
                def interrupt(phase, op):
                    if op['action']=='restore' and phase=='recorded':
                        raise fixtures.Interrupted()
                self.m.checkpoint = interrupt
                with self.assertRaises(fixtures.Interrupted):
                    self.restore(record)
                self.m.checkpoint = lambda *a:None
                op = next(o for o in self.m._operations() if o['action']=='restore' and o['vm_id']==record['uuid'])
                result = self.restore(record)
                self.assertTrue(result['ok'], result)
                self.assertTrue(result['replayed'])
                self.assertEqual(result['operation_id'], op['operation_id'])
                target = self.board.data/'instances'/record['id']
                self.assertEqual({p.name:p.read_bytes() for p in target.iterdir()}, before)
                self.assertEqual((target/'root.raw').stat().st_ino, inode)
                self.assertEqual(result['data']['configuration']['mac'], record['mac'])
                self.assertEqual(result['data']['ipv4'], record['ipv4'])
        self.assertEqual(sha(FILES/'defaults.json'), self.defaults_hash)
        self.assertEqual((self.board.data/'.first-install-state.json').read_bytes(), self.identity_state)

    def test_factory_restore_after_move_recovers_same_assets_and_generation(self):
        for record in self.records.values():
            with self.subTest(name=record['id']):
                archive = self.retire(record)
                root_hash = sha(archive/'root.raw')
                def interrupt(phase, op):
                    if op['action']=='restore' and phase=='moved':
                        raise fixtures.Interrupted()
                self.m.checkpoint = interrupt
                with self.assertRaises(fixtures.Interrupted):
                    self.restore(record)
                self.m.checkpoint = lambda *a:None
                result = self.restore(record)
                self.assertTrue(result['ok'], result)
                self.assertEqual(sha(self.board.data/'instances'/record['id']/'root.raw'), root_hash)
                self.assertEqual(result['data']['generation'], 2)

    def test_foreign_mac_lease_is_rejected_and_actual_own_mac_is_accepted(self):
        for record in self.records.values():
            with self.subTest(name=record['id']):
                archive = self.retire(record)
                before = sha(archive/'root.raw')
                lease = self.p.runtime/'dnsmasq.leases'
                lease.write_text(f"9999999999 02:00:00:00:00:01 {record['ipv4']} foreign *\n")
                for execute in (False, True):
                    self.error(self.restore(record, execute=execute), 'RESTORE_CONFLICT')
                self.assertEqual(sha(archive/'root.raw'), before)
                lease.write_text(f"9999999999 {record['mac']} {record['ipv4']} owned *\n")
                result = self.restore(record)
                self.assertTrue(result['ok'], result)

    def test_different_registered_uuid_cannot_share_factory_reserved_ip(self):
        record = self.records['ubuntu-dev']
        archive = self.retire(record)
        foreign = copy.deepcopy(record)
        foreign.update(id='foreign', name='foreign', hostname='foreign',
                       uuid=self.spec['vm_id'], mac='02:00:00:00:00:02')
        fixtures.fixture(self.board.data, foreign)
        self.error(self.restore(record), 'RESTORE_CONFLICT')
        self.assertTrue((archive/'root.raw').exists())

    def test_factory_exemption_requires_planned_uuid_and_actual_mac(self):
        record = self.records['ubuntu-dev']
        archive = self.retire(record)
        path = self.board.data/'.first-install-state.json'
        original = path.read_text()
        for field, value in [('uuid', self.spec['vm_id']), ('mac','02:00:00:00:00:01')]:
            with self.subTest(field=field):
                state = json.loads(original)
                state['identities'][record['id']][field] = value
                path.write_text(json.dumps(state))
                self.error(self.restore(record), 'RESTORE_CONFLICT')
                self.assertTrue((archive/'root.raw').exists())
        path.write_text(original)
        self.assertTrue(self.restore(record)['ok'])

    def test_general_auto_ip_and_restore_still_exclude_both_factory_addresses(self):
        self.retire(self.records['ubuntu-dev'])
        shutil.copytree(self.f.data/'templates/source', self.board.data/'templates/source')
        shutil.copytree(self.f.data/'catalog', self.board.data/'catalog')
        with self.linux_boundaries():
            created = self.m.call('create', self.spec, execute=True)
        self.assertTrue(created['ok'], created)
        identity = created['data']['vm_id']
        self.assertNotIn(created['data']['ipv4'], ['192.168.77.10','192.168.77.20'])
        self.assertTrue(self.m.call('retire', {'vm_id':identity}, execute=True,
                                  idempotency_key='general-retire')['ok'])
        restored = self.m.call('restore', {'vm_id':identity}, execute=True,
                               idempotency_key='general-restore')
        self.assertTrue(restored['ok'], restored)
        self.assertEqual(restored['data']['ipv4'], created['data']['ipv4'])


class LinuxDeploymentVersionTest(FollowupFixture):
    def manifest(self, *, deployment='1.0.0', legacy='0.9.0', include_deployment=True, include_legacy=True):
        source = self.f.data/'templates/source'
        path = source/'template.json'
        meta = json.loads(path.read_text())
        if include_deployment:
            meta['template_version'] = deployment
        else:
            meta.pop('template_version', None)
        if include_legacy:
            meta['version'] = legacy
        else:
            meta.pop('version', None)
        path.chmod(0o644)
        path.write_text(json.dumps(meta))
        path.chmod(0o444)
        descriptor = self.f.data/'catalog/ubuntu-24.04-arm64/1.0.0.json'
        value = json.loads(descriptor.read_text())
        value['manifest_sha256'] = fixtures.catalog.sha(path)
        descriptor.write_text(json.dumps(value))
        return source

    def assert_real_create_fields(self, source):
        before = {p.name:sha(p) for p in source.iterdir()}
        with self.linux_boundaries() as commands:
            result = self.m.call('create', self.spec, execute=True)
        self.assertTrue(result['ok'], result)
        self.assertFalse(any(c[0]=='cp' for c in commands))
        target = self.f.data/'instances'/self.f.key
        self.assertNotEqual((target/'root.raw').stat().st_ino, (source/'root.raw').stat().st_ino)
        for name in ('root.raw','Image','initrd','boot.json','kernel.config'):
            self.assertEqual(sha(target/name), before[name])
        self.assertEqual({p.name:sha(p) for p in source.iterdir()}, before)
        record = json.loads((self.f.data/'instances'/self.f.key/'instance.json').read_text())
        rows = [result['data'], self.m.call('get', {'vm_id':self.spec['vm_id']})['data']['vm'],
                self.m.call('list')['data']['vms'][0]]
        self.assertEqual(record['creation_spec']['template_version'], '1.0.0')
        for row in [record, *rows]:
            self.assertEqual(row['template_id'], self.spec['template_id'])
            self.assertEqual(row['template_version'], '1.0.0')
            self.assertEqual(row['guest_kind'], 'linux')
        return result

    def test_real_preparation_preserves_deployment_version_over_legacy_version(self):
        self.assert_real_create_fields(self.manifest())

    def test_real_preparation_supports_legacy_version_only(self):
        self.assert_real_create_fields(self.manifest(include_deployment=False, legacy='1.0.0'))

    def test_real_preparation_supports_explicit_deployment_version_only(self):
        self.assert_real_create_fields(self.manifest(include_legacy=False))

    def test_invalid_preferred_version_is_rejected_before_operation_or_workspace(self):
        for value in (None, [], 'not-a-version'):
            with self.subTest(value=value):
                self.manifest(deployment=value, legacy='1.0.0')
                with self.linux_boundaries():
                    result = self.m.call('create', self.spec, execute=True)
                self.error(result, 'ARTIFACT_INVALID')
                self.assertFalse((self.f.data/'operations').exists())
                self.assertEqual(list((self.f.data/'instances').iterdir()), [])

    def test_direct_preparation_rejects_selected_version_mismatch_before_workspace(self):
        source = self.manifest()
        record = fixtures.record()
        record.update(id='custom', name='custom', hostname='custom', template='source', template_id=self.spec['template_id'], template_version='2.0.0')
        workspace = self.f.data/'instances/.selected-version-test'
        args = Namespace(data_root=self.f.data, id='custom', deployment_seed=False, spec=None)
        with self.linux_boundaries(), self.assertRaisesRegex(ValueError, 'selected template version'):
            preparation.make_instance(args, vm=vm, defaults_path=FILES/'defaults.json',
                record_override=record, identity=record['uuid'], workspace_path=workspace,
                template_directory=source)
        self.assertFalse(workspace.exists())
        self.assertFalse((self.f.data/'instances/custom').exists())

    def test_manager_rejects_mismatched_published_identity_fields_without_removing_disk(self):
        for field, value in [('template_id','debian-13-arm64'), ('template_version','0.9.0'), ('guest_kind','zephyr'), ('guest_kind',None)]:
            with self.subTest(field=field):
                # Each corruption case has its own real source/registry/operation.
                f = fixtures.ManagementTest()
                f.setUp()
                previous = self.f, self.p, self.m, self.spec
                self.f, self.p, self.m = f, f.p, f.m
                self.spec = {k:v for k,v in f.spec.items() if k!='ipv4'}
                try:
                    with self.linux_boundaries():
                        real_prepare = self.m.preparer
                        def corrupt(op):
                            real_prepare(op)
                            path = f.data/'instances'/f.key/'instance.json'
                            record = json.loads(path.read_text())
                            if value is None:
                                record.pop(field)
                            else:
                                record[field] = value
                            path.write_text(json.dumps(record))
                        self.m.preparer = corrupt
                        result = self.m.call('create', self.spec, execute=True)
                    self.error(result, 'RECOVERY_REQUIRED')
                    self.assertTrue((f.data/'instances'/f.key/'root.raw').exists())
                finally:
                    f.doCleanups()
                    self.f, self.p, self.m, self.spec = previous
