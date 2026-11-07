"""geometry 범위 비교와 full context/scene 사용권의 분리를 검산한다. SQL 연결은 별도다."""

from dataclasses import replace
from hashlib import sha256
import unittest
from uuid import uuid4

from xenova_vehicle.storage.contracts import StorageContext,PersistenceError,canonical,json_bytes
from xenova_vehicle.storage.geometry_scope import CatalogGeometryScope,PiGeometryOrigin
from xenova_vehicle.runtime.lifecycle_messages import LifecyclePeer
from xenova_vehicle.runtime.local_envelope import encode_structure
from xenova_vehicle.runtime.raw_leases import Endpoint
from xenova_vehicle.runtime.storage_copy import CopyLease
from xenova_vehicle.runtime.storage_jobs import StorageJobs
from xenova_vehicle.runtime.storage_messages import FIELDS,load_from_prepare,make_load_request,prepare_payload


def identity():return str(uuid4())


class GeometryScopeTests(unittest.TestCase):
    def setUp(self):
        self.binding=dict(service_instance=identity(),mapper_worker=identity(),mapper_instance=identity(),raw_binding_sha256='a'*64,
            map_epoch=identity(),frame_epoch=identity(),odom_epoch=identity(),configuration='config',calibration='b'*64,primitive='c'*64,
            scene_admission_ref=None,source_binding_revision='1')
        b=self.binding
        self.origin=PiGeometryOrigin('rc-car',identity(),identity(),identity(),identity(),b['odom_epoch'],b['calibration'])
        self.context=StorageContext(identity(),b['mapper_worker'],b['mapper_instance'],b['map_epoch'],b['frame_epoch'],b['odom_epoch'],
            self.origin.pi_host_boot,b['configuration'],b['calibration'],b['primitive'],canonical(b),geometry_origin=self.origin.fields)

    def changed(self,**fields):
        b={**self.binding,**fields}
        names=dict(mapper_worker='mapper_worker',mapper_instance='origin_mapper_instance',map_epoch='map_epoch',frame_epoch='frame_epoch',
            odom_epoch='odom_epoch',configuration='configuration',calibration='calibration',primitive='primitive_sha256')
        values={names[key]:value for key,value in fields.items() if key in names}
        origin=replace(self.origin,**{key:fields[key] for key in ('odom_epoch','calibration') if key in fields})
        values['geometry_origin']=origin.fields
        return replace(self.context,source_binding=canonical(b),**values)

    def test_scene_only_transition_preserves_geometry_scope_but_original_full_context_is_unchanged(self):
        original=self.context.bytes
        next_context=self.changed(scene_admission_ref=dict(admission_id=identity(),revision='7'),source_binding_revision='2')
        before=CatalogGeometryScope.from_context(self.context,'SLOT',self.origin)
        after=CatalogGeometryScope.from_context(next_context,'SLOT',self.origin)
        self.assertNotEqual(self.context.sha256,next_context.sha256)
        self.assertNotEqual(before.context_sha256,after.context_sha256)
        self.assertTrue(before.same_geometry(after));self.assertEqual(before.sha256,after.sha256)
        self.assertEqual(self.context.bytes,original);self.assertEqual(json_bytes(original,4096)['source_binding'],self.binding)
        copied=before.fields;copied['mapper_worker']=identity()
        self.assertEqual(before.fields['mapper_worker'],self.context.mapper_worker)

    def test_mapper_raw_map_frame_odom_calibration_config_and_kind_changes_are_distinct(self):
        before=CatalogGeometryScope.from_context(self.context,'SLOT',self.origin)
        for key,value in (('mapper_worker',identity()),('mapper_instance',identity()),('raw_binding_sha256','d'*64),
                ('map_epoch',identity()),('frame_epoch',identity()),('odom_epoch',identity()),('configuration','other'),
                ('calibration','e'*64),('primitive','f'*64),('service_instance',identity())):
            with self.subTest(key=key):self.assertFalse(before.same_geometry(CatalogGeometryScope.from_context(self.changed(**{key:value}),'SLOT')))
        self.assertFalse(before.same_geometry(CatalogGeometryScope.from_context(self.context,'LANE',self.origin)))
        self.assertFalse(before.same_geometry(CatalogGeometryScope.from_context(replace(self.context,database_uuid=identity()),'SLOT',self.origin)))

    def test_actual_pi_origin_changes_are_distinct_even_when_odom_epoch_is_identical(self):
        before=CatalogGeometryScope.from_context(self.context,'SLOT',self.origin)
        for key in ('vehicle','gateway_instance','device_session','pi_session'):
            changed=replace(self.origin,**{key:'another-car' if key=='vehicle' else identity()})
            with self.subTest(key=key):self.assertFalse(before.same_geometry(CatalogGeometryScope.from_context(replace(self.context,geometry_origin=changed.fields),'SLOT',changed)))
        changed=replace(self.origin,pi_host_boot=identity())
        with self.assertRaises(PersistenceError):CatalogGeometryScope.from_context(self.context,'SLOT',changed)
        self.assertFalse(before.same_geometry(CatalogGeometryScope.from_context(replace(self.context,pi_host_boot=changed.pi_host_boot,geometry_origin=changed.fields),'SLOT',changed)))

    def test_unknown_or_legacy_source_json_is_not_stripped_into_a_supported_scope(self):
        for binding in (dict(test=True),dict(self.binding,registration_revision='9'),dict(self.binding,time_lease_revision='20')):
            with self.assertRaises(PersistenceError):CatalogGeometryScope.from_context(replace(self.context,source_binding=canonical(binding)),'SLOT',self.origin)
        self.assertEqual(PiGeometryOrigin.from_fields(self.origin.fields),self.origin)

    def test_legacy_roundtrip_retains_exact_original_fields_bytes_and_hash(self):
        original=self.context.fields;original.pop('geometry_origin')
        raw=canonical(original);legacy=StorageContext.from_fields(original)
        self.assertIsNone(legacy.geometry_origin);self.assertNotIn('geometry_origin',legacy.fields)
        self.assertEqual(legacy.bytes,raw);self.assertEqual(legacy.sha256,sha256(raw).hexdigest())
        self.assertEqual(StorageContext.from_fields(legacy.fields).bytes,raw)
        with self.assertRaises(PersistenceError):CatalogGeometryScope.from_context(legacy,'SLOT',self.origin)
        with self.assertRaises(PersistenceError):StorageContext.from_fields(dict(original,geometry_origin=None))
        for key,value in (('pi_host_boot',identity()),('odom_epoch',identity()),('calibration','f'*64)):
            with self.subTest(key=key),self.assertRaises(PersistenceError):replace(self.context,geometry_origin=dict(self.origin.fields,**{key:value}))

    def test_paired_origin_bind_and_current_load_keep_publication_hash_and_exact_db_fields(self):
        peer=LifecyclePeer(self.binding['service_instance'],identity(),identity(),'D','a'*64)
        storage=dict(service_instance=peer.service_instance,storage_worker=peer.worker_instance,storage_profile_sha256='a'*64,
            database_uuid=self.context.database_uuid,db_schema_version=1)
        jobs=StorageJobs(peer,storage,None,None,clock_us=lambda:1000)
        pubsha=sha256(canonical(self.binding)).hexdigest()

        def bind(origin):
            payload=dict(binding_revision='1',binding_sha256=pubsha,binding=self.binding,geometry_origin=origin)
            return encode_structure(dict(version=1,service_instance=peer.service_instance,channel_epoch=peer.channel_epoch,sender_instance=peer.service_instance,
                message_sequence='1',kind='BIND',binding_domain='PUBLICATION',binding_revision='1',request_id=identity(),issued_j_us='1000',expires_j_us='2000',payload=payload))

        ack,=jobs.bind_publication(bind(self.origin.fields))
        self.assertTrue(ack['payload']['accepted']);self.assertEqual(ack['payload']['binding_sha256'],pubsha)
        self.assertEqual(jobs.geometry_origin,self.context.geometry_origin)
        with self.assertRaises(PersistenceError):jobs.bind_publication(bind(replace(self.origin,gateway_instance=identity()).fields))
        reference=dict(checkpoint_job_id=identity(),durable_revision='1',manifest_sha256='f'*64,manifest_length=200,tile_count=0,
            map_epoch=self.context.map_epoch,frame_epoch=self.context.frame_epoch,odom_epoch=self.context.odom_epoch,pi_host_boot=self.context.pi_host_boot,
            configuration=self.context.configuration,calibration=self.context.calibration,primitive_sha256=self.context.primitive_sha256,
            origin_mapper_instance=self.context.origin_mapper_instance)
        request=make_load_request(storage,mapper_worker=self.context.mapper_worker,origin_mapper_instance=self.context.origin_mapper_instance,
            job_id=identity(),issued_us=1000,expires_us=2000,checkpoint_ref=reference,selection=dict(kind='CHECKPOINT_PAGE',cursor=0),
            purpose='CURRENT_MAP',publication=self.binding,geometry_origin=self.context.geometry_origin_fields)
        self.assertEqual(request.job.context,self.context)
        body=prepare_payload(request)['body'];self.assertEqual(set(body),set(FIELDS['PREPARE'].split()))
        lease=CopyLease(peer.service_instance,identity(),1,Endpoint(peer.channel_epoch,peer.worker_instance),Endpoint(identity(),self.context.mapper_worker),identity())
        reconstructed=load_from_prepare(storage,body,lease,1000,2000,publication=self.binding,geometry_origin=jobs.geometry_origin)
        self.assertEqual(reconstructed,request)
        with self.assertRaises(ValueError):load_from_prepare(storage,body,lease,1000,2000,publication=self.binding)
