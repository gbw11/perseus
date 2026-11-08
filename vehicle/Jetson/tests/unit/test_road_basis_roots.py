"""D의 versioned META root와 명시 ContextRecord edge만 검사한다.

model/source는 opaque 원 metadata이며 road geometry/current 승인을 만들지 않는다.
SQLite fixture도 실제 service lock·sensor·5초 처리율의 증거는 아니다.
"""

import ast
from copy import deepcopy
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
from uuid import UUID

from xenova_vehicle.storage import mapper_state as module
from xenova_vehicle.storage.mapper_state import mapper_state_roots,encode_mapper_state
from xenova_vehicle.storage.contracts import (StorageContext,StorageJob,LoadRequest,CatalogReservation,
    PersistenceError,canonical,json_bytes)
from xenova_vehicle.storage.records import ContextRecord,ProvenanceRecord,PROVENANCE,EvidenceTable,CatalogRecord,CheckpointPayload
from xenova_vehicle.storage.repository import PersistenceStore


def uid(n):return '75000000-0000-4000-8000-%012x'%n


def model(a,b):
    # root helper는 이 미해석 필드를 채우거나 LaneModelCandidates로 승인하지 않는다.
    return dict(road=None,source=None,lane_ids=None,lane_revisions=None,model_revision=None,
        crossing=None,provenance_ids=[a,b],original_context_sha256=None)


def basis():
    return dict(profile='road-basis-1',geometry_scope={'profile':'OPAQUE_SQL_FIXTURE'},reference_id='opaque-road-root-fixture',
        reference_revision=1,points_f32=struct.pack('<ffff',0.,0.,1.,0.).hex(),point_error_m=[.01,.01],segment_error_m=[.01],
        events=[dict(model=model(1,2),resolution_m=.02)],latest=model(3,4),
        handoff=dict(model=model(5,6),resolution_m=.02,reason='OPAQUE_ROOT_ONLY_FIXTURE'))


class RoadBasisRootsTests(unittest.TestCase):
    def sql_fixture(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        connection=sqlite3.connect(str(Path(directory.name)/'roots.sqlite3'),isolation_level=None)
        self.addCleanup(connection.close)
        connection.execute('PRAGMA application_id=1481528881');connection.execute('PRAGMA user_version=1')
        connection.execute('PRAGMA journal_mode=DELETE');connection.execute('PRAGMA synchronous=FULL')
        connection.execute('CREATE TABLE storage_metadata (singleton INTEGER PRIMARY KEY CHECK(singleton=1),format_tag TEXT NOT NULL,database_uuid TEXT NOT NULL,schema_version INTEGER NOT NULL,allocator_highwater TEXT NOT NULL)')
        connection.execute('CREATE TABLE checkpoints (checkpoint_job_id TEXT PRIMARY KEY,complete INTEGER NOT NULL CHECK(complete IN(0,1)),metadata_json TEXT NOT NULL)')
        connection.execute('INSERT INTO storage_metadata VALUES (1,?,?,1,?)',('xenova-storage-bootstrap-1',uid(1),'1'))
        context=StorageContext(uid(1),uid(2),uid(3),uid(4),uid(5),uid(6),uid(7),
            'explicit-config','explicit-calibration','a'*64,canonical({'profile':'EXPLICIT_SQL_ROOT_FIXTURE'}))
        store=PersistenceStore(connection,context.database_uuid,expires_us=6_000_000,clock_us=lambda:1_000_000)
        def job(n):return StorageJob(context,uid(n),1_000_000,6_000_000)
        method=ContextRecord(canonical(dict(kind='METRIC',body=dict(profile='parking-method-1',
            method_manifest_sha256='a'*64,storage_context_sha256=context.sha256,calibration_profile_sha256=None,
            vehicle_bundle_sha256=context.calibration,coordinate='MAP_X_FORWARD_Y_LEFT_REAR_AXLE',map_frame_id=context.frame_epoch,
            body={},geometry_profile={},association_profile={}))))
        originals=ContextRecord(canonical(dict(kind='METRIC',body=dict(profile='parking-originals-1',
            method_context_sha256=method.sha256,storage_context_sha256=context.sha256,catalog_revision='1',sources=[],entries=[]))))
        def proof(identity,source=originals):
            raw=struct.pack('<16sQQ',UUID(context.mapper_worker).bytes,identity,123).ljust(128,b'\0')
            return ProvenanceRecord(identity,PROVENANCE.pack(bytes.fromhex(source.sha256),1,0,1,0,
                UUID(context.pi_host_boot).bytes,100,200,identity,0,0,0,0,raw,0,bytes(16)))
        return connection,context,store,job,method,originals,proof

    def test_subsystem_generic_and_pipeline_roots_preserve_every_original_model_without_mutation(self):
        road=basis();before=canonical(road)
        for state in ({'road_basis':road},{'profile':'opaque-generic-fixture','road_basis':road}):
            self.assertEqual(mapper_state_roots(state),(1,2,3,4,5,6))
        state=dict(profile='mapper-pipeline-state-1',reference_provenance={'b'*64:9},alignment_provenance=10,road_basis=road)
        self.assertEqual(mapper_state_roots(state),(1,2,3,4,5,6,9,10))
        self.assertEqual(canonical(road),before)
        self.assertIsNone(road['events'][0]['model']['source'])
        self.assertEqual(mapper_state_roots(None),())
        self.assertEqual(mapper_state_roots({'profile':'old-opaque-metadata'}),())
        for protected in ('reference_provenance','alignment_provenance'):
            with self.assertRaises(PersistenceError):mapper_state_roots({'profile':'unknown',protected:None,'road_basis':road})
        legacy=dict(profile='mapper-pipeline-state-1',reference_provenance={'b'*64:9},alignment_provenance=10)
        self.assertEqual(mapper_state_roots(legacy),(9,10))

    def test_exact_state_event_latest_handoff_and_model_shapes_are_required(self):
        original=basis();invalid=[]
        for key in original:
            value=deepcopy(original);del value[key];invalid.append(value)
        invalid.extend((None,{},dict(original,profile='road-basis-2'),dict(original,unknown=True)))
        for member in ('events','latest','handoff'):
            value=deepcopy(original)
            if member=='events':value[member]=[{'model':model(1,2)}]
            elif member=='latest':value[member]={}
            else:value[member]={'model':model(5,6),'resolution_m':.02}
            invalid.append(value)
            value=deepcopy(original)
            target=value['events'][0]['model'] if member=='events' else value[member] if member=='latest' else value[member]['model']
            del target['provenance_ids'];invalid.append(value)
            value=deepcopy(original)
            target=value['events'][0]['model'] if member=='events' else value[member] if member=='latest' else value[member]['model']
            target['unknown']=True;invalid.append(value)
        for value in invalid:
            with self.subTest(value=value),self.assertRaises(PersistenceError):mapper_state_roots({'road_basis':value})

    def test_all_model_id_domains_event_cap_and_byte_column_caps_are_bounded(self):
        for member in ('events','latest','handoff'):
            for ids in ([True,2],[0,2],[-1,2],[1<<63,2],[1],[1,2,3],None):
                value=basis();target=value['events'][0]['model'] if member=='events' else value[member] if member=='latest' else value[member]['model']
                target['provenance_ids']=ids
                with self.subTest(member=member,ids=ids),self.assertRaises(PersistenceError):mapper_state_roots({'road_basis':value})
        value=basis();value['events']=[dict(model=model(2*i+1,2*i+2),resolution_m=.02) for i in range(64)]
        value['latest']=model(127,128);value['handoff']['model']=model(129,130)
        self.assertEqual(mapper_state_roots({'road_basis':value}),tuple(range(1,131)))
        value['events'].append(dict(model=model(131,132),resolution_m=.02))
        with self.assertRaises(PersistenceError):mapper_state_roots({'road_basis':value})
        for change in ('point_cap','point_shape','segment_shape','resolution_bool','resolution_nonfinite','error_bool','error_negative'):
            value=basis()
            if change=='point_cap':value['points_f32']=struct.pack('<ff',0.,0.).hex()*65
            elif change=='point_shape':value['point_error_m']=[]
            elif change=='segment_shape':value['segment_error_m']=[]
            elif change=='resolution_bool':value['events'][0]['resolution_m']=True
            elif change=='resolution_nonfinite':value['handoff']['resolution_m']=float('inf')
            elif change=='error_bool':value['point_error_m'][0]=True
            else:value['segment_error_m'][0]=-1.
            with self.subTest(change=change),self.assertRaises(PersistenceError):mapper_state_roots({'road_basis':value})

    def test_root_module_imports_no_runtime_numpy_or_catalog_geometry(self):
        tree=ast.parse(Path(module.__file__).read_text(encoding='utf-8'))
        imports=[]
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):imports.extend(alias.name for alias in node.names)
            elif isinstance(node,ast.ImportFrom):imports.append(node.module or '')
        self.assertFalse(any(any(part in name for part in ('runtime','numpy','catalog','geometry')) for name in imports))

    def test_actual_sql_checkpoint_load_retains_unlinked_prefix_and_handoff_roots_and_method_bytes(self):
        connection,context,store,job,method,originals,proof=self.sql_fixture()
        store.reserve_provenance(job(10),uid(11),block_count=8,current_context_sha256=context.sha256)
        reservation=CatalogReservation(context.database_uuid,context.map_epoch,'LANE',1,1,3,uid(12),context.mapper_worker,context.sha256)
        reserve=job(13);payload=CheckpointPayload.build(reserve,evidence=EvidenceTable(reservations=(reservation,),catalog_highwaters=(3,1)),intent='CATALOG_RESERVATION')
        self.assertEqual(store.commit(reserve,payload,current_context_sha256=context.sha256).status,'COMMITTED')
        ref,pages=encode_mapper_state({'profile':'opaque-generic-fixture','road_basis':basis()})
        catalog=(CatalogRecord('LANE',1,1,0,3,b'opaque-latest-lane'),CatalogRecord('LANE',2,1,0,4,b'opaque-latest-lane'))
        evidence=EvidenceTable(tuple(proof(i) for i in range(1,7)),(originals,method)+pages,catalog,catalog_highwaters=(3,1))
        cp=job(14);payload=CheckpointPayload.build(cp,evidence=evidence,scene={'mapper_state':ref},durable_cutoff_pi_us=200)
        self.assertEqual(store.commit(cp,payload,current_context_sha256=context.sha256).status,'COMMITTED')
        reference=store.selected_checkpoint(cp.job_id,expires_us=6_000_000)
        request=LoadRequest(job(15),canonical(reference),canonical({'kind':'CHECKPOINT_PAGE','cursor':0}))
        before=connection.execute('SELECT allocator_highwater FROM storage_metadata').fetchone()
        loaded=store.load(request,current_context_sha256=context.sha256)
        self.assertEqual({r.provenance_id for r in loaded.payload.evidence.catalog},{3,4})
        self.assertEqual({r.provenance_id for r in loaded.payload.evidence.provenance},set(range(1,7)))
        contexts={r.sha256:r.payload for r in loaded.payload.evidence.contexts}
        self.assertEqual(contexts[method.sha256],method.payload)
        self.assertNotIn('a'*64,contexts)  # asset SHA는 ContextRecord dependency가 아니다.
        self.assertEqual(connection.execute('SELECT allocator_highwater FROM storage_metadata').fetchone(),before)
        connection.execute('DELETE FROM provenance_records WHERE provenance_id=1')
        with self.assertRaises(PersistenceError):store.load(request,current_context_sha256=context.sha256)

    def test_only_known_exact_context_dependency_is_followed_and_missing_foreign_or_corrupt_fails(self):
        connection,context,store,job,method,originals,proof=self.sql_fixture()
        def closure(source,target=None):
            contexts={source.sha256:source}
            if target is not None:contexts[target.sha256]=target
            return store._closure({1},{1:proof(1,source)},contexts)[1]
        with self.assertRaises(PersistenceError):closure(originals)
        self.assertEqual(closure(originals,method)[method.sha256].payload,method.payload)
        for change in ('foreign_profile','foreign_storage','unknown_source_key'):
            target_fields=json_bytes(method.payload,4096);source_fields=json_bytes(originals.payload,4096)
            if change=='foreign_profile':target_fields['body']['profile']='scene-source-context-1'
            elif change=='foreign_storage':target_fields['body']['storage_context_sha256']='f'*64
            else:source_fields['body']['unknown']=True
            target=ContextRecord(canonical(target_fields));source_fields['body']['method_context_sha256']=target.sha256
            source=ContextRecord(canonical(source_fields))
            with self.subTest(change=change),self.assertRaises(PersistenceError):closure(source,target)
        corrupt=json_bytes(method.payload,4096);corrupt['body']['method_manifest_sha256']='b'*64
        connection.execute('INSERT INTO source_contexts VALUES (?,?)',(method.sha256,canonical(corrupt)))
        with self.assertRaises(PersistenceError):closure(originals)
        unknown=ContextRecord(canonical({'kind':'METRIC','body':{'profile':'opaque-other-profile','method_context_sha256':'f'*64}}))
        self.assertEqual(set(closure(unknown)),{unknown.sha256})
        scene=ContextRecord(canonical({'kind':'METRIC','body':{'profile':'scene-source-context-1','method_manifest_sha256':'f'*64}}))
        self.assertEqual(set(closure(scene)),{scene.sha256})
