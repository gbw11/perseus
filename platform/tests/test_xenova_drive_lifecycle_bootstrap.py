"""Actual receipt/CAS code; only Store and execution observation are external."""
import json
import unittest
from unittest.mock import patch
from uuid import uuid4
import test_xenova_telemetry as telemetry_fixture

class DriveAppBootstrapTest(unittest.TestCase):
    def setUp(self):
        self.f=telemetry_fixture.TelemetryBootstrapTest('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        f=self.f;f.apply('telemetry.json');self.writes=[]
        class Store:
            channel='drive';quiescent=True
            def apply(inner,*args):self.writes.append(args)
        self.store=Store();self.path=f.directory/'drive.json'
        f.module.provision(f.context,f.credential,f.source,self.path,store=self.store,observe=lambda:f.context,
            channel='drive',guest_boot_receipt=f.directory/'telemetry.json',drive_profile='warm-drive-1')
        self.initial=self.read();self.key=str(uuid4())
    def read(self):return json.loads(self.path.read_text())
    def rotate(self,**kwargs):
        f=self.f
        return f.module.reinitialize_app(f.context,f.credential,f.source,self.path,app='autonomous',
            expected_instance=self.initial['drive_apps']['autonomous'],request_id=self.key,
            store=kwargs.get('store',self.store),observe=kwargs.get('observe',lambda:f.context))
    def test_root_cas_replay_preserves_other_app_execution_boot_and_transport(self):
        first=self.rotate();receipt=self.read();calls=len(self.writes)
        assert first['instance_id']!=self.initial['drive_apps']['autonomous']
        assert receipt['drive_apps']['manual']==self.initial['drive_apps']['manual']
        for field in ('boot_id','publisher_id','receipt_id','context','credential_source'):
            self.assertEqual(receipt[field],self.initial[field])
        replay=self.rotate();self.assertTrue(replay['replayed']);self.assertEqual(len(self.writes),calls)
        self.assertEqual(replay['instance_id'],first['instance_id'])
        f=self.f
        f.module.reinitialize_app(f.context,f.credential,f.source,self.path,app='manual',
            expected_instance=self.initial['drive_apps']['manual'],request_id=str(uuid4()),store=self.store,observe=lambda:f.context)
        calls=len(self.writes);self.assertTrue(self.rotate()['replayed']);self.assertEqual(len(self.writes),calls)
        self.assertEqual(self.read()['drive_apps']['autonomous'],first['instance_id'])
        self.key=str(uuid4())
        with self.assertRaisesRegex(ValueError,'instance changed'):self.rotate()
    def test_uncertain_store_does_not_mint_another_identity(self):
        f=self.f
        class Broken:
            channel='drive';quiescent=True
            def apply(inner,*args):self.writes.append(args);raise OSError('effect uncertain')
        with self.assertRaises(OSError):self.rotate(store=Broken())
        receipt=self.read();self.assertEqual(receipt['phase'],'uncertain');calls=len(self.writes)
        with self.assertRaisesRegex(ValueError,'reconciliation'):self.rotate()
        self.assertEqual(self.read()['drive_apps'],receipt['drive_apps']);self.assertEqual(len(self.writes),calls)
        expected=f.module.bootstrap_payload(f.context,f.credential,receipt,'drive')
        self.assertEqual(expected['manual_instance_id'],self.initial['drive_apps']['manual'])
    def test_failed_prepare_and_changed_execution_cannot_send(self):
        f=self.f;calls=len(self.writes)
        with patch.object(f.module,'save_receipt',side_effect=OSError(28,'no space')):
            with self.assertRaises(OSError):self.rotate()
        self.assertEqual(len(self.writes),calls);self.assertEqual(self.read(),self.initial)
        with self.assertRaisesRegex(ValueError,'current execution'):
            self.rotate(observe=lambda:{**f.context,'execution_id':str(uuid4())})
        self.assertEqual(len(self.writes),calls)
    def test_existing_profile_cannot_be_silently_changed(self):
        f=self.f
        with self.assertRaisesRegex(ValueError,'profile cannot'):
            f.module.provision(f.context,f.credential,f.source,self.path,store=self.store,observe=lambda:f.context,
                channel='drive',guest_boot_receipt=f.directory/'telemetry.json',drive_profile='verified-drive-1')

    def test_profile_selection_cannot_be_ignored_by_other_cli_modes(self):
        f=self.f
        args=['--channel','drive','--vm-id',f.context['vm_id'],'--credentials-file',str(f.directory/'private.json'),
            '--receipt',str(self.path),'--expected-host-boot-id',f.context['host_boot_id'],
            '--expected-execution-id',f.context['execution_id'],'--drive-profile','warm-drive-1']
        for extra in (['--reconcile'],['--reinitialize-app','autonomous','--expected-app-instance',
                self.initial['drive_apps']['autonomous'],'--app-request-id',self.key]):
            with self.assertRaises(SystemExit) as error:f.module.main(args+extra)
            self.assertEqual(error.exception.code,2)
