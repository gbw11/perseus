"""Spring 생성 계약의 실제 파일·레지스트리·작업/IP 예약 회귀. Xen/마운트만 대역."""
import io
import json
from pathlib import Path
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import test_xenova_management as fixtures
from first_install_fixture import ROOT, FILES
import xenova_management as management
import xenova_management_cli as cli

FIRST = '550e8400-e29b-41d4-a716-446655440000'
SECOND = '650e8400-e29b-41d4-a716-446655440000'


class SpringContractTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ManagementTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.spec = {k:v for k,v in self.f.spec.items() if k != 'ipv4'}
        self.spec['name'] = '개발용 Ubuntu'

    def create(self, spec=None):
        result = self.f.m.call('create', spec or self.spec, execute=True)
        self.assertTrue(result['ok'], result)
        return result

    def op(self, result):
        return self.f.m._read(self.f.data/'operations'/(result['operation_id']+'.json'))

    def error(self, result, code):
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['error']['code'], code, result)

    def test_external_uuid_display_name_safe_paths_cfg_and_common_vm_fields(self):
        created = self.create()
        row = created['data']
        self.assertEqual(row['vm_id'], FIRST)
        self.assertEqual(row['name'], self.spec['name'])
        self.assertEqual(row['ipv4'], '192.168.77.2')
        config = row['configuration']
        self.assertEqual(config['uuid'], FIRST)
        self.assertEqual(config['name'], management.internal_name(FIRST))
        self.assertEqual(config['hostname'], config['name'])
        text = self.f.p.config(config)
        self.assertIn(FIRST, text)
        self.assertNotIn(self.spec['name'], text)
        self.assertIn('target=', text)
        self.assertTrue((self.f.data/'instances'/config['id']/'root.raw').exists())
        get = self.f.m.call('get', {'vm_id':FIRST})['data']['vm']
        listed = self.f.m.call('list')['data']['vms'][0]
        for other in (get, listed):
            for key in ('vm_id','name','template_id','template_version','guest_kind','vcpus','memory_mib','ipv4'):
                self.assertEqual(other[key], row[key], (key, other))
        self.assertEqual(get['app_readiness'], 'unknown')

    def test_missing_invalid_uuid_and_bad_display_name_leave_no_records_or_ip(self):
        for change in ({'vm_id':None}, {'vm_id':'invalid'}, {'vm_id':[]}, {'name':'bad\nname'},
                       {'name':'\x00'}, {'name':' '}, {'name':'x'*64}):
            self.error(self.f.m.call('create', {**self.spec, **change}, execute=True), 'INVALID_SPEC')
        self.error(self.f.m.call('create', {k:v for k,v in self.spec.items() if k!='vm_id'}, execute=True), 'INVALID_SPEC')
        self.assertFalse((self.f.data/'operations').exists())
        self.assertEqual(list((self.f.data/'instances').iterdir()), [])

    def test_uuid_spelling_replays_same_operation_without_external_retry_key(self):
        first = self.create()
        second = self.create({**self.spec, 'vm_id':FIRST.replace('-','').upper()})
        self.assertEqual(first['operation_id'], second['operation_id'])
        self.assertTrue(second['replayed'])
        self.assertEqual(self.f.copies, 1)
        self.assertEqual(second['data']['ipv4'], first['data']['ipv4'])
        self.error(self.f.m.call('create', {**self.spec,'memory_mib':768}, execute=True), 'IDEMPOTENCY_CONFLICT')

    def test_duplicate_display_names_are_independent_uuid_assets(self):
        first = self.create()
        second = self.create({**self.spec, 'vm_id':SECOND})
        self.assertEqual(first['data']['name'], second['data']['name'])
        self.assertNotEqual(first['data']['configuration']['id'], second['data']['configuration']['id'])
        self.assertNotEqual(first['data']['configuration']['mac'], second['data']['configuration']['mac'])
        self.assertNotEqual(first['data']['ipv4'], second['data']['ipv4'])
        self.assertEqual(len(self.f.p.records(validate_assets=False)), 2)

    def test_display_text_cannot_inject_cfg_path_or_hostname(self):
        result = self.create({**self.spec,'name':'../same " path [display]'})
        record = result['data']['configuration']
        self.assertEqual(record['id'], management.internal_name(FIRST))
        self.assertNotIn('[display]', self.f.p.config(record))

    def test_plan_does_not_allocate_and_lock_contention_is_not_ip_exhaustion(self):
        from xenova_runtime import operation_lock
        result = self.f.m.call('create', self.spec)
        self.assertTrue(result['ok'], result)
        self.assertFalse((self.f.data/'operations').exists())
        with operation_lock(self.f.run):
            self.error(self.f.m.call('create', self.spec, execute=True), 'OPERATION_IN_PROGRESS')

    def test_initial_ip_and_configuration_do_not_change_on_replay(self):
        first = self.create()
        path = self.f.data/'instances'/self.f.key/'instance.json'
        record = json.loads(path.read_text())
        record.update(display_name='변경된 이름', memory_mib=768, autostart=True)
        path.write_text(json.dumps(record))
        replay = self.create()
        self.assertEqual(replay['data']['name'], '변경된 이름')
        self.assertEqual(replay['data']['memory_mib'], 768)
        self.assertEqual(replay['data']['initial_spec']['name'], self.spec['name'])
        self.assertEqual(replay['operation_id'], first['operation_id'])

    def test_reserved_factory_registered_pending_and_dhcp_addresses_excluded(self):
        self.f.run.mkdir()
        (self.f.run/'dhcp-hosts').write_text('02:00:00:00:00:01,192.168.77.2,external,infinite\n')
        (self.f.run/'dnsmasq.leases').write_text('9999999999 02:00:00:00:00:02 192.168.77.3 lease *\n')
        first = self.create()
        self.assertEqual(first['data']['ipv4'], '192.168.77.4')
        self.f.m.checkpoint = lambda phase, op: (_ for _ in ()).throw(fixtures.Interrupted()) if phase=='recorded' else None
        with self.assertRaises(fixtures.Interrupted):
            self.f.m.call('create', {**self.spec,'vm_id':SECOND}, execute=True)
        op = next(o for o in self.f.m._operations() if o['vm_id']==SECOND)
        reserved = op['assigned'].copy()
        self.f.m.checkpoint = lambda *args: None
        third_id = '750e8400-e29b-41d4-a716-446655440000'
        third = self.create({**self.spec,'vm_id':third_id})
        self.assertNotIn(third['data']['ipv4'], [reserved['ipv4'],'192.168.77.2','192.168.77.3'])
        resumed = self.create({**self.spec,'vm_id':SECOND})
        self.assertEqual(resumed['data']['ipv4'], reserved['ipv4'])
        self.assertEqual(self.op(resumed)['assigned'], reserved)
        self.assertNotIn(reserved['ipv4'], ['192.168.77.10','192.168.77.20'])

    def test_ip_exhaustion_and_unknown_template_do_not_write_operations(self):
        self.f.run.mkdir()
        (self.f.run/'dhcp-hosts').write_text(''.join(
            f'02:00:00:00:00:01,192.168.77.{i},reserved,infinite\n' for i in range(2,100)))
        exhausted = self.f.m.call('create', self.spec, execute=True)
        self.error(exhausted, 'IP_POOL_EXHAUSTED')
        import runpy
        transport = runpy.run_path(str(ROOT/'tests/contracts/spring_manager_bridge.py'))['transport']
        self.assertEqual(transport(exhausted)['status'], 507)
        self.assertEqual(transport(exhausted)['body']['code'], 'InsufficientResources')
        self.error(self.f.m.call('create', {**self.spec,'template_id':'unknown'}, execute=True), 'TEMPLATE_NOT_FOUND')
        self.assertFalse((self.f.data/'operations').exists())

    def test_retire_restore_purge_address_ownership_and_old_create_rejection(self):
        first = self.create()
        ip = first['data']['ipv4']
        self.assertTrue(self.f.call('retire', {'vm_id':FIRST})['ok'])
        self.error(self.f.m.call('create', self.spec, execute=True), 'TARGET_CHANGED')
        second = self.create({**self.spec,'vm_id':SECOND})
        self.assertNotEqual(second['data']['ipv4'], ip)
        restored = self.f.call('restore', {'vm_id':FIRST})
        self.assertTrue(restored['ok'], restored)
        self.assertEqual(restored['data']['ipv4'], ip)
        self.assertTrue(self.f.call('retire', {'vm_id':FIRST},key='retire-2')['ok'])
        self.assertTrue(self.f.call('purge', {'vm_id':FIRST,'confirm_vm_id':FIRST})['ok'])
        self.error(self.f.m.call('create', self.spec, execute=True), 'VM_PURGED')
        third = self.create({**self.spec,'vm_id':'750e8400-e29b-41d4-a716-446655440000'})
        self.assertEqual(third['data']['ipv4'], ip)
        self.assertTrue(self.f.m.call('operation', {'operation_id':first['operation_id']})['ok'])

    def test_dhcp_failure_after_publish_retries_same_ip_uuid_and_disk(self):
        original = self.f.p.command
        def command(args, **kwargs):
            if args[:2] == ['systemctl','reload']:
                raise fixtures.vm.PlatformError('DHCP reload failed')
            return original(args, **kwargs)
        self.f.p.command = command
        failed = self.f.m.call('create', self.spec, execute=True)
        self.assertFalse(failed['ok'], failed)
        op = self.op(failed)
        self.assertEqual(op['phase'], 'dhcp-pending')
        disk = self.f.data/'instances'/self.f.key/'root.raw'
        inode = disk.stat().st_ino
        self.f.p.command = original
        resumed = self.create()
        self.assertEqual(resumed['operation_id'], failed['operation_id'])
        self.assertEqual(self.op(resumed)['assigned'], op['assigned'])
        self.assertEqual(disk.stat().st_ino, inode)
        self.assertEqual(self.f.copies, 1)

    def test_post_publish_response_loss_keeps_reservation(self):
        self.f.m.checkpoint = lambda phase, op: (_ for _ in ()).throw(fixtures.Interrupted()) if phase=='published' else None
        with self.assertRaises(fixtures.Interrupted):
            self.f.m.call('create', self.spec, execute=True)
        op = self.f.m._operations()[0]
        self.f.m.checkpoint = lambda *args: None
        replay = self.create()
        self.assertEqual(replay['operation_id'], op['operation_id'])
        self.assertEqual(replay['data']['ipv4'], op['assigned']['ipv4'])
        self.assertEqual(self.f.copies, 1)

    def test_cli_auto_ip_has_one_json_and_no_retry_key_required(self):
        output = io.StringIO()
        with redirect_stdout(output):
            value = cli.main(['create','--vm-id',FIRST,'--name',self.spec['name'],
                '--template-id','ubuntu-24.04-arm64','--template-version','1.0.0',
                '--vcpus','1','--memory-mib','512','--auto-ip','--execute','--json'], manager=self.f.m)
        self.assertEqual(json.loads(output.getvalue()), value)
        self.assertEqual(value['data']['vm_id'], FIRST)

    def test_current_spring_http_fields_and_manager_bridge_are_distinct(self):
        import importlib.util
        import re
        path = ROOT/'tests/contracts/spring_manager_bridge.py'
        spec = importlib.util.spec_from_file_location('spring_bridge', path)
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        request = json.loads((ROOT/'tests/contracts/spring-create-request.json').read_text())
        source = (ROOT/'backend/src/main/java/com/xenova/backend/vm/infrastructure/Dom0LaunchRequest.java').read_text()
        fields = re.search(r'record Dom0LaunchRequest\((.*?)\)', source, re.S).group(1)
        names = re.findall(r'(?:String|int)\s+(\w+)', fields)
        names = {re.sub(r'([A-Z])', lambda m:'_'+m[1].lower(), name) for name in names}
        self.assertEqual(names, {'vm_id','name','template_id','template_version','vcpus','memory_mib'})
        self.assertEqual(set(request), names)
        http_request = {key:request[key] for key in names}
        created = self.f.m.call('create', http_request, execute=True)
        self.assertTrue(created['ok'], created)
        view = self.f.m.call('get', {'vm_id':http_request['vm_id']})['data']['vm']
        self.assertEqual(view['configuration']['display_name'], request['name'])
        calls = [
            {'spec':request}, {'spec':request},
            {'spec':{**request,'memory_mib':768}},
            {'action':'get','spec':{'vm_id':FIRST}},
            {'action':'list'},
            {'spec':{**request,'vm_id':SECOND,'template_id':'zephyr-xenvm-starter-arm64','memory_mib':16}},
            {'spec':{**request,'vm_id':'750e8400-e29b-41d4-a716-446655440000','template_id':'missing'}},
            {'action':'retire','spec':{'vm_id':FIRST},'idempotency_key':'retire-one'},
            {'spec':request},
            {'action':'purge','spec':{'vm_id':FIRST,'confirm_vm_id':FIRST},'idempotency_key':'purge-one'},
            {'spec':request},
        ]
        with patch.object(fixtures.ManagementTest, 'prepare', side_effect=AssertionError('metadata must use the real Manager preparer')):
            rows = bridge.run(calls)
        self.assertEqual([row['status'] for row in rows], [200,200,409,200,200,200,400,200,409,200,409])
        for other in (rows[1]['body'], rows[3]['body'], rows[4]['body'][0]):
            self.assertEqual(other['vm_id'], FIRST)
            self.assertEqual(other['ipv4'], rows[0]['body']['ipv4'])
            self.assertEqual(other['template_id'], request['template_id'])
        self.assertTrue(rows[1]['envelope']['replayed'])
        self.assertIsNone(rows[5]['body']['ipv4'])
        self.assertEqual(rows[5]['body']['guest_kind'], 'zephyr')
        self.assertEqual(rows[5]['body']['memory_mib'], 16)
        self.assertEqual(rows[6]['body']['code'], 'InvalidArgument')

    def test_restore_checks_foreign_dhcp_lease_but_accepts_own_mac(self):
        first = self.create()
        record = first['data']['configuration']
        self.assertTrue(self.f.call('retire', {'vm_id':FIRST})['ok'])
        lease = self.f.run/'dnsmasq.leases'
        lease.write_text(f"9999999999 02:00:00:00:00:01 {record['ipv4']} external *\n")
        failed = self.f.call('restore', {'vm_id':FIRST})
        self.error(failed, 'RESTORE_CONFLICT')
        lease.write_text(f"9999999999 {record['mac']} {record['ipv4']} own *\n")
        restored = self.f.call('restore', {'vm_id':FIRST})
        self.assertTrue(restored['ok'], restored)
        self.assertEqual(restored['data']['ipv4'], record['ipv4'])

    def test_recorded_ip_conflict_on_resume_does_not_choose_another_address(self):
        self.f.m.checkpoint = lambda phase,op: (_ for _ in ()).throw(fixtures.Interrupted()) if phase=='recorded' else None
        with self.assertRaises(fixtures.Interrupted):
            self.f.m.call('create', self.spec, execute=True)
        op = self.f.m._operations()[0]
        self.f.m.checkpoint = lambda *args: None
        (self.f.run/'dnsmasq.leases').write_text(
            f"9999999999 02:00:00:00:00:01 {op['assigned']['ipv4']} conflicting *\n")
        failed = self.f.m.call('create', self.spec, execute=True)
        self.error(failed, 'IP_CONFLICT')
        self.assertEqual(self.op(failed)['assigned'], op['assigned'])
        self.assertEqual(self.f.copies, 0)
        (self.f.run/'dnsmasq.leases').write_text('')
        resumed = self.create()
        self.assertEqual(resumed['data']['ipv4'], op['assigned']['ipv4'])

    def test_corrupt_address_inventory_fails_closed_without_reservation(self):
        self.f.run.mkdir()
        path = self.f.run/'dnsmasq.leases'
        path.write_text('not a lease\n')
        self.error(self.f.m.call('create', self.spec, execute=True), 'RECOVERY_REQUIRED')
        self.assertFalse((self.f.data/'operations').exists())
        self.assertEqual(path.read_text(), 'not a lease\n')

    def test_changed_internal_path_in_operation_is_not_followed(self):
        self.f.m.checkpoint = lambda phase,op: (_ for _ in ()).throw(fixtures.Interrupted()) if phase=='recorded' else None
        with self.assertRaises(fixtures.Interrupted):
            self.f.m.call('create', self.spec, execute=True)
        self.f.m.checkpoint = lambda *args: None
        path = next((self.f.data/'operations').glob('*.json'))
        op = json.loads(path.read_text())
        op['assigned']['internal_name'] = '../outside'
        path.write_text(json.dumps(op))
        self.error(self.f.m.call('create', self.spec, execute=True), 'RECOVERY_REQUIRED')
        self.assertEqual(self.f.copies, 0)
        del op['assigned']['internal_name']
        path.write_text(json.dumps(op))
        self.error(self.f.m.call('create', self.spec, execute=True), 'RECOVERY_REQUIRED')
        self.assertEqual(self.f.copies, 0)

    def test_pool_configuration_agrees_with_ssh_forwarding_allowlist(self):
        defaults = json.loads((FILES/'defaults.json').read_text())
        auth = (FILES/'xenova-auth.conf').read_text()
        pool, reserved = self.f.m._ip_inventory({})
        self.assertEqual(defaults['network']['reserved_ipv4'], ['192.168.77.10','192.168.77.20'])
        self.assertTrue(all(f'{ip}:22' in auth for ip in pool))
        self.assertIn('AllowTcpForwarding local', auth)

    def test_factory_uuid_exception_and_template_metadata_remain_readable(self):
        from first_install_fixture import Board
        board = Board(self.f.data.parent/'factory')
        board.grow_mount()
        board.factory()
        board.p.register()
        xen = fixtures.FakeXen(board.data,{})
        platform = fixtures.vm.Platform(board.data,self.f.data.parent/'factory-run',command=xen,
                                        defaults_path=FILES/'defaults.json')
        manager = management.Manager(platform)
        result = manager.call('list')
        self.assertTrue(result['ok'], result)
        rows = result['data']['vms']
        self.assertEqual({row['name'] for row in rows}, {'ubuntu-dev','debian-dev'})
        defaults = json.loads((ROOT/'tests/platform/fixtures/legacy-defaults.json').read_text())
        for row in rows:
            original = next(d for d in defaults['instances'] if d['id']==row['name'])
            self.assertEqual(row['template_id'], original['template_id'])
            self.assertEqual(row['template_version'], original['template_version'])
            self.assertEqual(row['ipv4'], original['ipv4'])
            self.assertNotEqual(row['vm_id'], FIRST)
        self.assertEqual(rows[0]['guest_kind'], 'linux')
