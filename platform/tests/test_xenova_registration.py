"""공개 등록 reader/CLI. 외부 관측·전송 없이 원 metadata와 유한 재대조를 검증한다."""
from contextlib import redirect_stdout
import io
import json
import unittest
from unittest.mock import patch

from first_install_fixture import Board
import test_xenova_recovery_foundation as foundation
from test_xenova_platform import fixture, record
import xenova_management as management
import xenova_management_cli as cli
from xenova_runtime import operation_lock


class RegistrationTest(unittest.TestCase):
    def setUp(self):
        self.f=foundation.FoundationTest('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.m,self.p,self.r=self.f.m,self.f.p,self.f.r

    def rows(self):return self.f.ok(self.m.call('registrations'))['registrations']

    def test_exact_fields_current_saved_resources_and_no_observation_intent_secrets_or_writes(self):
        path=self.p.data/'instances'/self.r['id']/'instance.json'
        value=json.loads(path.read_text());value.update(display_name='same display',vcpus=3,memory_mib=768)
        path.write_text(json.dumps(value))
        # 등록과 무관한 intent 손상도 이 조회의 의존성이 아니다.
        (self.p.data/'control'/(self.r['uuid']+'.json')).write_text('{broken')
        before={str(p):p.read_bytes() for p in self.p.data.rglob('*') if p.is_file()}
        runtime=list(self.p.runtime.rglob('*'))
        with (patch.object(self.p,'execution',side_effect=AssertionError('execution')),
              patch.object(self.p,'boot_id_reader',side_effect=AssertionError('host')),
              patch.object(self.p,'command',side_effect=AssertionError('Xen')),
              patch.object(management,'read_intent',side_effect=AssertionError('intent'))):
            row=self.rows()[0]
        self.assertEqual(set(row),{'vm_id','name','guest_kind','template_id','template_version','vcpus','memory_mib','assigned_ipv4'})
        self.assertEqual((row['name'],row['vcpus'],row['memory_mib']),('same display',3,768))
        self.assertEqual(row['template_id'],self.f.f.spec['template_id'])
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.p.data.rglob('*') if p.is_file()})
        self.assertEqual(runtime,list(self.p.runtime.rglob('*')))

    def test_factory_no_journal_duplicate_display_names_and_legacy_null_version(self):
        other=record('debian-dev');other.pop('template_version',None)
        other['display_name']=self.r.get('display_name',self.r['name'])
        fixture(self.p.data,other)
        rows=self.rows();self.assertEqual(len(rows),2)
        old=next(r for r in rows if r['vm_id']==other['uuid'])
        self.assertIsNone(old['template_version']);self.assertEqual(old['name'],rows[0]['name'])
        self.assertEqual(self.f.ok(self.m.call('registrations',{'vm_id':other['uuid']}))['registration'],old)

    def test_lifecycle_and_unpublished_scope_running_stopped_and_removed(self):
        self.f.start();self.assertEqual(len(self.rows()),1)
        self.f.stop();self.assertEqual(len(self.rows()),1)
        private=self.p.data/'instances/.unpublished';private.mkdir();(private/'instance.json').write_text('{broken')
        self.assertEqual(len(self.rows()),1)
        self.f.ok(self.m.call('retire',{'vm_id':self.f.vm_id},execute=True,idempotency_key='registration-retire'))
        self.assertEqual(self.rows(),[])
        self.f.fails(self.m.call('registrations',{'vm_id':self.f.vm_id}),'VM_NOT_FOUND')
        self.f.ok(self.m.call('purge',{'vm_id':self.f.vm_id,'confirm_vm_id':self.f.vm_id},execute=True,idempotency_key='registration-purge'))
        self.assertEqual(self.rows(),[])

    def test_metadata_corruption_is_error_then_normal_recovery(self):
        path=self.p.data/'instances'/self.r['id']/'instance.json';saved=path.read_bytes()
        path.write_text('{broken')
        self.assertFalse(self.m.call('registrations')['ok'])
        path.write_bytes(saved);self.assertEqual(len(self.rows()),1)

    def test_actual_zephyr_factory_profile_keeps_reserved_ip_and_absence_null(self):
        from test_xenova_zephyr import bundle
        from test_xenova_lifecycle import application_bundle
        import xenova_zephyr as zephyr
        source=application_bundle(bundle(self.p.data/'z-source'),self.p.data/'rtos-templates/z-registration')
        manifest=source/'firmware.json';meta=json.loads(manifest.read_text())
        meta.update(id='z-registration',kind='zephyr-xen-firmware',version='1.0.0')
        manifest.chmod(0o644);manifest.write_text(json.dumps(meta));manifest.chmod(0o444)
        identity='850e8400-e29b-41d4-a716-446655440001'
        record=zephyr.new_instance(self.p.data,'z-registration',source,{},identity=identity)
        path=self.p.data/'instances/z-registration/instance.json'
        self.assertIsNone(self.f.ok(self.m.call('registrations',{'vm_id':identity}))['registration']['assigned_ipv4'])
        record.update(ipv4='192.168.77.90',mac='02:16:3e:00:00:90',hostname='z-registration')
        path.write_text(json.dumps(record))
        with patch.object(zephyr,'bundle_info',side_effect=AssertionError('payload validation')):
            row=self.f.ok(self.m.call('registrations',{'vm_id':identity}))['registration']
        self.assertEqual(row['guest_kind'],'zephyr');self.assertEqual(row['assigned_ipv4'],'192.168.77.90')
        self.assertEqual(row['memory_mib'],16)

    def test_retire_between_read_and_publish_retries_bounded_then_returns_current_scope(self):
        original=management.ReadContext.validate_one;changed=[]
        def validate(context,identity):
            if not changed:
                changed.append(True)
                self.f.ok(self.m.fork().call('retire',{'vm_id':identity},execute=True,idempotency_key='registration-race'))
            return original(context,identity)
        with patch.object(management.ReadContext,'validate_one',validate):self.assertEqual(self.rows(),[])

    def test_cli_read_only_and_old_notify_all_variants_are_removed_before_call(self):
        for args in (['registrations'],['registrations','--json'],['registrations',self.f.vm_id,'--json']):
            with redirect_stdout(io.StringIO()):self.assertTrue(cli.main(args,self.m)['ok'])
        for args in (['registrations','--execute'],['registration-notify',self.f.vm_id],
                     ['registration-notify',self.f.vm_id,'--execute','--json'],['registration_notify',self.f.vm_id]):
            with patch.object(self.m,'call',side_effect=AssertionError('removed/invalid CLI called manager')):
                with redirect_stdout(io.StringIO()),self.assertRaises(SystemExit):cli.main(args,self.m)
        self.f.fails(self.m.call('registration-notify',{'vm_id':self.f.vm_id}),'INVALID_SPEC')

    def test_registered_query_and_real_stop_progress_under_common_lock(self):
        self.f.start()
        with operation_lock(self.p.runtime):self.assertEqual(len(self.rows()),1)
        result=self.m.fork().call('stop',{'vm_id':self.f.vm_id},idempotency_key='after-registration')
        self.f.ok(result);self.assertEqual(len(self.f.physical('shutdown')),1)

    def test_factory_completion_returns_only_first_new_registration_opportunities(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as root:
            b=Board(Path(root)/'board');b.grow_mount();b.factory()
            notices=b.p.register();self.assertEqual(len(notices),2)
            self.assertEqual(b.p.register(),[])


if __name__=='__main__':unittest.main()
