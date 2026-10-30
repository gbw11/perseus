"""수락 뒤 복사 검증 실패의 원 기록·부분 파일·새 프로세스 재확인 경계를 검증한다."""
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from test_xenova_spring_followup import FollowupFixture
import test_xenova_management as fixtures
import prepare_instance as preparation
import xenova_management as management


class CreationFailureTest(FollowupFixture):
    def change(self, name):
        source = self.f.data / 'templates/source' / name
        before = source.read_bytes()
        source.chmod(0o644)
        content = bytearray(before)
        content[0] ^= 1
        source.write_bytes(content)
        source.chmod(0o444)
        return source, before

    def failed_copy(self):
        source, original = self.change('root.raw')
        with self.linux_boundaries(), patch.object(preparation, 'prepare_instance') as configure:
            response = self.m.call('create', self.spec, execute=True)
        self.error(response, 'RECOVERY_REQUIRED')
        configure.assert_not_called()
        op = self.m._operations()[0]
        path = self.f.data / 'operations' / (op['operation_id']+'.json')
        disk = self.f.data / op['owned_workspace'] / self.f.key / 'root.raw'
        self.assertEqual(response['operation_id'], op['operation_id'])
        self.assertEqual(op['status'], 'RECOVERY_REQUIRED')
        self.assertEqual(op['phase'], 'preparing')
        self.assertEqual(op['error'], response['error'])
        self.assertIn('SHA-256 mismatch: root.raw',op['error']['message'])
        self.assertTrue(disk.is_file())
        self.assertFalse((self.f.data/'instances'/self.f.key).exists())
        self.assertEqual(self.controls(), [])
        return source, original, op, path, disk

    def controls(self):
        return [args for args in self.f.xen.calls if args[:2] in (
            ['xl','create'],['xl','shutdown'],['xl','reboot'],['xl','destroy'])]

    def cold_recheck(self, op, *, execute):
        code = r'''
import json,sys
from pathlib import Path
from unittest.mock import patch
from first_install_fixture import FILES
from test_xenova_platform import FakeXen
import xenova_vm as vm
import xenova_management as management
import prepare_instance as preparation
data,run,vm_id,key,execute=sys.argv[1:]
xen=FakeXen(Path(data),{})
p=vm.Platform(Path(data),Path(run),command=xen,defaults_path=FILES/'defaults.json')
m=management.Manager(p)
with patch.object(vm,'storage_check'), patch.object(preparation,'_copy_verified',side_effect=AssertionError('must not recopy')) as copies:
    before=m.call('operation',{'idempotency_key':key})
    replay=m.call('create',json.loads(sys.stdin.read()),execute=execute=='yes')
    after=m.call('operation',{'idempotency_key':key})
    print(json.dumps({'before':before,'replay':replay,'after':after,'copy_calls':copies.call_count,
        'physical_calls':[a for a in xen.calls if a[:2] in (['xl','create'],['xl','shutdown'],['xl','destroy'])]}))
'''
        child = subprocess.run([sys.executable,'-B','-c',code,str(self.f.data),str(self.f.run),op['vm_id'],
            op['idempotency_key'],'yes' if execute else 'no'], input=json.dumps(self.spec),
            text=True,capture_output=True,timeout=30)
        self.assertEqual(child.returncode,0,child.stderr)
        return json.loads(child.stdout)

    def test_corrupt_payload_is_rejected_after_recording_and_before_guest_setup(self):
        self.failed_copy()

    def test_corrupt_boot_asset_retains_the_already_verified_root_without_publication(self):
        self.change('Image')
        with self.linux_boundaries(), patch.object(preparation,'prepare_instance') as configure:
            response = self.m.call('create',self.spec,execute=True)
        self.error(response,'RECOVERY_REQUIRED')
        configure.assert_not_called()
        op = self.m._operations()[0]
        stage = self.f.data/op['owned_workspace']/self.f.key
        self.assertEqual((stage/'root.raw').read_bytes(),(self.f.data/'templates/source/root.raw').read_bytes())
        self.assertTrue((stage/'Image').exists())
        self.assertEqual(op['error'],response['error'])
        self.assertFalse((self.f.data/'instances'/self.f.key).exists())

    def copy_write_failure(self, code):
        original_write = os.write
        written = []

        def write(fd, data):
            if Path(os.readlink('/proc/self/fd/'+str(fd))).name=='root.raw':
                if written:
                    raise OSError(code,'injected copy write failure')
                count = original_write(fd,data[:1])
                written.append(count)
                return count
            return original_write(fd,data)

        with self.linux_boundaries(), patch.object(preparation.os,'write',side_effect=write):
            response = self.m.call('create',self.spec,execute=True)
        self.error(response,'RECOVERY_REQUIRED')
        op = self.m._operations()[0]
        self.assertEqual(written,[1])
        self.assertEqual(op['error'],response['error'])
        self.assertIn('errno='+str(code),op['error']['message'])
        disk = self.f.data/op['owned_workspace']/self.f.key/'root.raw'
        before = disk.stat().st_ino,disk.read_bytes()
        self.assertEqual(len(before[1]),1)
        self.assertFalse((self.f.data/'instances'/self.f.key).exists())
        result = self.cold_recheck(op,execute=True)
        self.assertEqual(result['after']['data']['operation'],op)
        self.assertEqual(result['copy_calls'],0)
        self.assertEqual((disk.stat().st_ino,disk.read_bytes()),before)

    def test_enospc_during_copy_retains_partial_bytes_and_recorded_reason_after_restart(self):
        self.copy_write_failure(errno.ENOSPC)

    def test_eio_during_copy_retains_partial_bytes_and_recorded_reason_after_restart(self):
        self.copy_write_failure(errno.EIO)

    def test_same_request_in_fresh_process_keeps_first_failure_and_partial_bytes(self):
        source, original, op, path, disk = self.failed_copy()
        before_op, before_disk = path.read_bytes(), (disk.stat().st_ino,disk.read_bytes())
        # 원본을 고쳐도 이미 실패한 부분 준비를 자동으로 재초기화하지 않는다.
        source.chmod(0o644);source.write_bytes(original);source.chmod(0o444)
        result = self.cold_recheck(op,execute=True)
        self.assertFalse(result['replay']['ok'])
        self.assertEqual(result['replay']['operation_id'],op['operation_id'])
        self.assertTrue(result['replay']['replayed'])
        self.assertEqual(result['before']['data']['operation'],op)
        self.assertEqual(result['after']['data']['operation'],op)
        self.assertEqual(result['after']['data']['effective_status'],'RECOVERY_REQUIRED')
        self.assertEqual(result['copy_calls'],0)
        self.assertEqual(result['physical_calls'],[])
        self.assertEqual(path.read_bytes(),before_op)
        self.assertEqual((disk.stat().st_ino,disk.read_bytes()),before_disk)
        self.assertEqual(len(self.m._operations()),1)

    def test_plan_and_operation_query_are_read_only_after_failed_copy(self):
        _, _, op, path, disk = self.failed_copy()
        before = path.read_bytes(),disk.read_bytes()
        result = self.cold_recheck(op,execute=False)
        self.assertTrue(result['replay']['ok'])
        self.assertTrue(result['replay']['data']['plan'])
        self.assertEqual(result['replay']['data']['operation'],op)
        self.assertEqual(result['after']['data']['operation'],op)
        self.assertEqual((path.read_bytes(),disk.read_bytes()),before)

    def test_same_vm_with_different_spec_cannot_replace_a_failed_origin(self):
        _, _, op, path, disk = self.failed_copy()
        before = path.read_bytes(),disk.read_bytes()
        response = self.m.call('create',{**self.spec,'memory_mib':1024},execute=True)
        self.error(response,'IDEMPOTENCY_CONFLICT')
        self.assertEqual(response['operation_id'],op['operation_id'])
        self.assertEqual((path.read_bytes(),disk.read_bytes()),before)

    def test_failure_before_workspace_can_resume_only_the_original_recorded_identity(self):
        source = self.f.data/'templates/source/boot.json'
        original = source.read_bytes()
        altered = original.replace(b'"test"',b'"best"')
        self.assertNotEqual(altered,original)
        source.chmod(0o644);source.write_bytes(altered);source.chmod(0o444)
        with self.linux_boundaries():
            failed = self.m.call('create',self.spec,execute=True)
        self.error(failed,'RECOVERY_REQUIRED')
        op = self.m._operations()[0]
        self.assertFalse((self.f.data/op['owned_workspace']).exists())
        source.chmod(0o644);source.write_bytes(original);source.chmod(0o444)
        fresh = management.Manager(self.p,defaults_path=self.m.defaults_path)
        with self.linux_boundaries(), patch.object(preparation,'_copy_verified',wraps=preparation._copy_verified) as copies:
            recovered = fresh.call('create',self.spec,execute=True)
        self.assertTrue(recovered['ok'],recovered)
        self.assertEqual(recovered['operation_id'],op['operation_id'])
        self.assertTrue(recovered['replayed'])
        after = self.m._operations()[0]
        for key in ('operation_id','idempotency_key','request','initial_spec','spec_hash','assigned','template','owned_workspace'):
            self.assertEqual(after[key],op[key])
        self.assertEqual(sum(call.args[0].name=='root.raw' for call in copies.call_args_list),1)

    def record_failure_write_error(self, code):
        self.change('root.raw')
        original_save = self.m._save_op

        def save(op,phase=None):
            if op.get('status')=='RECOVERY_REQUIRED':
                raise OSError(code,'injected failure-record write error')
            return original_save(op,phase)

        with self.linux_boundaries(), patch.object(self.m,'_save_op',side_effect=save):
            response = self.m.call('create',self.spec,execute=True)
        self.error(response,'RECOVERY_REQUIRED')
        self.assertIn('not confirmed durable',response['error']['message'])
        op = self.m._operations()[0]
        self.assertEqual(response['operation_id'],op['operation_id'])
        self.assertEqual(op['status'],'RUNNING')
        disk = self.f.data/op['owned_workspace']/self.f.key/'root.raw'
        self.assertTrue(disk.is_file())
        before = disk.stat().st_ino,disk.read_bytes()
        result = self.cold_recheck(op,execute=True)
        self.assertEqual(result['before']['data']['effective_status'],'RECOVERY_REQUIRED')
        self.assertEqual(result['replay']['operation_id'],op['operation_id'])
        self.assertEqual(result['after']['data']['operation']['status'],'RECOVERY_REQUIRED')
        self.assertEqual(result['after']['data']['operation']['assigned'],op['assigned'])
        self.assertEqual(result['copy_calls'],0)
        self.assertEqual((disk.stat().st_ino,disk.read_bytes()),before)

    def test_enospc_on_failure_record_stays_unconfirmed_and_blocks_after_restart(self):
        self.record_failure_write_error(errno.ENOSPC)

    def test_eio_on_failure_record_stays_unconfirmed_and_blocks_after_restart(self):
        self.record_failure_write_error(errno.EIO)

    def test_interruption_after_failure_record_write_keeps_original_error_for_restart(self):
        self.change('root.raw')

        def interrupt(phase,op):
            if op['status']=='RECOVERY_REQUIRED': raise fixtures.Interrupted()

        self.m.checkpoint=interrupt
        with self.linux_boundaries(),self.assertRaises(fixtures.Interrupted):
            self.m.call('create',self.spec,execute=True)
        op = self.m._operations()[0]
        self.assertEqual(op['status'],'RECOVERY_REQUIRED')
        self.assertEqual(op['error']['code'],'RECOVERY_REQUIRED')
        result = self.cold_recheck(op,execute=True)
        self.assertEqual(result['after']['data']['operation'],op)
        self.assertEqual(result['copy_calls'],0)

    def test_published_before_result_is_recovered_without_another_copy(self):
        self.m.checkpoint=lambda phase,op: (_ for _ in ()).throw(fixtures.Interrupted()) if phase=='published' else None
        with self.linux_boundaries(),self.assertRaises(fixtures.Interrupted):
            self.m.call('create',self.spec,execute=True)
        op = self.m._operations()[0]
        disk = self.f.data/'instances'/self.f.key/'root.raw'
        before = disk.stat().st_ino,disk.read_bytes()
        self.m.checkpoint=lambda *args:None
        fresh = management.Manager(self.p,defaults_path=self.m.defaults_path)
        with patch.object(preparation,'_copy_verified',side_effect=AssertionError('must not recopy')):
            result = fresh.call('create',self.spec,execute=True)
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['operation_id'],op['operation_id'])
        self.assertTrue(result['replayed'])
        self.assertEqual((disk.stat().st_ino,disk.read_bytes()),before)
        self.assertEqual(len(self.m._operations()),1)
