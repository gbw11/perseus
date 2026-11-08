"""조사 전 RGB 기준점 계보와 조사 중 새 clearance를 구분하는 구조 회귀.

실제 collector의 SENSOR→GEOMETRY→REGISTRATION→FREE 형식을 명시한 typed DAG
fixture다. 이 시험은 카메라/collector 실행이나 interval certificate 물리 수락을
대신하지 않는다. 원 scope, 원 bytes와 조사 시간 역할의 소비 경계만 검사한다.
"""

from dataclasses import replace
import struct
import unittest
from uuid import UUID,uuid4

import test_mapper_sensor_state as sensor_fixture
from xenova_vehicle.runtime.scene import MapperScene,SceneEvidenceScope,SceneError,SurveyProfile
from xenova_vehicle.storage.contracts import StorageJob
from xenova_vehicle.storage.records import CELL,PROVENANCE,REGISTRATION,ProvenanceRecord,EvidenceTable,TileVersion,CheckpointPayload


def uid():return str(uuid4())


class SceneLineageTests(unittest.TestCase):
    def setUp(self):
        self.fixture=sensor_fixture.MapperSensorStateTests(methodName='runTest');self.fixture.setUp();f=self.fixture
        self.scope=SceneEvidenceScope(f.context,f.raw,f.calibration,'e'*64)
        self.context=self.scope.context_record('METRIC')
        self.profile=SurveyProfile('d'*64,1,4096,5000,.1,.05,.1,50000,.1,.1,500000,'LIVE')
        self.scene=MapperScene(f.context,f.fixture.source,self.profile,gateway_instance=f.fixture.gateway,evidence_scope=self.scope)
        self.begin,self.end=1_100_000,1_200_000
        rgb=f.raw.fields['stream_instances']['rgb_video'];depth=f.raw.fields['stream_instances']['depth_right']
        odom=f.context.geometry_origin_fields['device_session']
        self.records=(
            self.record(1,0,16,(1_089_800,1_090_200),1,raw=(odom,11,420000)),
            self.record(2,3,17,(1_090_000,1_090_002),1,parents=(1,),raw=(rgb,1,400000)),
            self.record(3,0,16,(1_109_800,1_110_200),2,raw=(odom,12,440000)),
            self.record(4,0,17,(1_110_000,1_110_002),2,parents=(3,),raw=(rgb,2,500000)),
            self.record(5,5,17,(1_110_000,1_110_002),2,parents=(3,4,2),body=self.registration()),
            self.record(6,2,19,(1_110_000,1_110_002),2,parents=(3,5),raw=(depth,2,500001)))

    def registration(self,**changes):
        c=self.fixture.context
        fields=dict(transform=(.01,0.,0.),position=.005,heading=.002)
        fields.update(changes)
        return REGISTRATION.pack(2,1,UUID(c.map_epoch).bytes,UUID(c.frame_epoch).bytes,UUID(c.odom_epoch).bytes,
            *fields['transform'],fields['position'],fields['heading'],100000,UUID(c.origin_mapper_instance).bytes,
            0.,0.,0.,.01,0.,0.)

    def record(self,identity,kind,mask,interval,sequence,*,parents=(),raw=None,body=None,host=None):
        if body is None:
            body=b'' if raw is None else struct.pack('<16sQQ',UUID(raw[0]).bytes,raw[1],raw[2])
            body=body.ljust(128,b'\0')
        return ProvenanceRecord(identity,PROVENANCE.pack(bytes.fromhex(self.context.sha256),mask,kind,
            int(raw is not None),len(parents),UUID(self.fixture.context.pi_host_boot if host is None else host).bytes,
            *interval,sequence,*(parents+(0,)*(4-len(parents))),body,0,bytes(16)))

    @staticmethod
    def changed(record,**updates):
        data=list(PROVENANCE.unpack(record.payload))
        offsets=dict(kind=2,mask=1,lower=6,upper=7,sequence=8,host=5,body=13)
        for name,value in updates.items():data[offsets[name]]=value
        return ProvenanceRecord(record.provenance_id,PROVENANCE.pack(*data))

    def payload(self,records=None,*,cell_sequence=None):
        records=self.records if records is None else records;root=records[-1]
        unknown=CELL.pack(255,0,0,0.,0.,0.,0,0,0,0,0,0)
        cell=CELL.pack(0,1,root.source_mask,0.,0.,.001,root.provenance_id,root.pi_lower,root.pi_upper,
            root.observation_sequence if cell_sequence is None else cell_sequence,1,0)
        tile=TileVersion(self.fixture.context.map_epoch,0,0,1,cell+unknown*4095)
        job=StorageJob(self.fixture.context,uid(),1_050_000,1_150_000)
        return CheckpointPayload.build(job,(tile,),EvidenceTable(tuple(records),(self.context,)),durable_cutoff_pi_us=self.end)

    def inspect(self,payload=None):
        return self.scene._inspect(self.payload() if payload is None else payload,begin_pi=self.begin,end_pi=self.end)

    def test_pre_survey_reference_and_odom_are_lineage_but_free_and_current_rgb_remain_new(self):
        self.assertLess(self.records[1].pi_upper,self.begin)
        self.assertEqual(self.inspect(),(1,3,0))
        # 역사 RGB만으로 required RGB를 충족할 수 없다. 현재 depth는 여전히 새 FREE다.
        depth=self.fixture.raw.fields['stream_instances']['depth_right']
        direct=self.record(6,2,19,(1_110_000,1_110_002),2,parents=(3,2),raw=(depth,2,500001))
        self.scene.profile=replace(self.profile,required_sensor_mask=3)
        with self.assertRaisesRegex(SceneError,'required sensors'):
            self.inspect(self.payload((*self.records[:4],direct)))

    def test_old_free_or_changed_original_cell_time_sequence_never_uses_history_exception(self):
        old=self.changed(self.records[-1],lower=self.begin-2,upper=self.begin-1)
        with self.assertRaisesRegex(SceneError,'old survey clearance'):
            self.inspect(self.payload((*self.records[:-1],old)))
        with self.assertRaisesRegex(SceneError,'immutable original provenance'):
            self.inspect(self.payload(cell_sequence=3))
        sensor=self.changed(self.records[-1],kind=0)
        with self.assertRaisesRegex(SceneError,'original direct depth/ToF clearance'):
            self.inspect(self.payload((*self.records[:-1],sensor)))

    def test_registration_requires_its_own_original_rgb_interval_and_sequence(self):
        reg=self.records[4]
        for rgb in (self.changed(self.records[3],sequence=1),
                    self.changed(self.records[3],lower=self.begin-2,upper=self.begin-1)):
            with self.subTest(rgb=rgb.provenance_id),self.assertRaisesRegex(SceneError,'current RGB'):
                self.inspect(self.payload((*self.records[:3],rgb,reg,self.records[-1])))
        no_rgb=self.record(5,5,17,(reg.pi_lower,reg.pi_upper),reg.observation_sequence,
            parents=(3,2),body=self.registration())
        with self.assertRaisesRegex(SceneError,'own original RGB'):
            self.inspect(self.payload((*self.records[:4],no_rgb,self.records[-1])))

    def test_history_cannot_hide_other_sensor_clearance_or_generic_mixed_aggregate(self):
        depth=self.fixture.raw.fields['stream_instances']['depth_right']
        for kind in (0,1,2):
            polluted=self.record(1,kind,2,(1_089_800,1_090_200),1,raw=(depth,1,420000))
            with self.subTest(kind=kind),self.assertRaises(SceneError):
                self.inspect(self.payload((polluted,*self.records[1:])))
        # header의 mask가 RGB/odom이라도 실제 depth raw key를 숨길 수 없다.
        hidden=self.record(1,0,16,(1_089_800,1_090_200),1,raw=(depth,1,420000))
        with self.assertRaisesRegex(SceneError,'PI_ODOM'):
            self.inspect(self.payload((hidden,*self.records[1:])))
        aggregate=self.record(2,4,16,(1_090_000,1_090_002),1,parents=(1,))
        with self.assertRaisesRegex(SceneError,'current RGB/static geometry|previous frozen geometry'):
            self.inspect(self.payload((self.records[0],aggregate,*self.records[2:])))

    def test_historical_host_raw_origin_future_time_and_invalid_numeric_union_still_reject(self):
        old=self.records[0]
        foreign=self.changed(old,host=UUID(uid()).bytes)
        with self.assertRaisesRegex(SceneError,'foreign/future'):
            self.inspect(self.payload((foreign,*self.records[1:])))
        raw=struct.pack('<16sQQ',UUID(uid()).bytes,1,420000).ljust(128,b'\0')
        wrong_raw=self.changed(old,body=raw)
        with self.assertRaisesRegex(SceneError,'actual registered'):
            self.inspect(self.payload((wrong_raw,*self.records[1:])))
        future=self.changed(self.records[1],upper=self.end+1)
        with self.assertRaisesRegex(SceneError,'foreign/future'):
            self.inspect(self.payload((self.records[0],future,*self.records[2:])))
        with self.assertRaises(ValueError):
            self.changed(self.records[4],body=self.registration(transform=(float('nan'),0.,0.)))
