"""별도 binary 통로와 원 Vehicle/D 문맥의 identity 제안. 이동/복원 승인은 검사하지 않는다."""

import socket
import struct
import unittest
from uuid import uuid4

from raw_binding_fixtures import binding_fields
from test_resident_perception import provisional
from xenova_drive_world import schema,encode
from xenova_vehicle.perception.calibration import CalibrationProfile
from xenova_vehicle.runtime.data_channel import DataLink,DataPacket,DataPeer,data_pair
from xenova_vehicle.runtime.raw_binding import RawBinding
from xenova_vehicle.runtime.source_identity import IdentityOwner,SourcePolicy,proposal_metadata


def uid():return str(uuid4())


def fixture():
    value=provisional().to_dict();value['vehicle_bundle_sha256']='a'*64
    profile=CalibrationProfile.from_dict(value)
    raw_fields=binding_fields();raw_fields['calibration_bundle']=profile.sha256
    raw=RawBinding(raw_fields)
    policy=SourcePolicy('test-car','test-configuration','a'*64,'b'*64,profile.sha256)
    sample=schema.VehicleSample(major=1,vehicle=policy.vehicle,host_boot=uid(),device_session=uid(),
        odom_epoch=uid(),sequence=1,source_sample=10,source_us=100,pi_sample_us=1000,
        clock_error_us=2,left_ticks=0,right_ticks=0,velocity_mps=0.,position_error_m=.01,
        heading_error_rad=.01,quality=3,calibration=policy.calibration,gateway_instance=uid(),pi_session=uid())
    sample.odom_pose.CopyFrom(schema.Pose(x=0.,y=0.,yaw=0.))
    bootstrap=dict(discovery_nonce=uid(),storage_profile_sha256='c'*64,status='EMPTY',database_uuid=uid(),
        schema_version=1,allocator_highwater='1',allocator_ready=True,selected_checkpoint=None)
    peer=DataPeer(raw.fields['service_instance'],uid(),uid())
    return profile,raw,policy,sample,bootstrap,peer,uid()


@unittest.skipUnless(hasattr(socket,'SOCK_SEQPACKET'),'actual Linux binary seqpacket required')
class SourceIdentityTests(unittest.TestCase):
    def links(self,peer):
        left,right=data_pair()
        return DataLink(left,peer,'M',clock=lambda:1000),DataLink(right,peer,'S',clock=lambda:1000)

    def test_binary_bounds_actual_send_peer_and_duplicate_bytes(self):
        peer=DataPeer(uid(),uid(),uid());mapper,supervisor=self.links(peer)
        try:
            packet=mapper.send('WORLD_CANDIDATE',{},bytes(32768),expires_us=2000)
            sent=mapper.flush()
            self.assertIs(sent.packet,packet)
            self.assertEqual(supervisor.poll()[0].body,bytes(32768))
            mapper.endpoint.send(packet.raw)
            self.assertEqual(supervisor.poll(),())
            self.assertEqual(supervisor.counts['duplicate'],1)
            with self.assertRaises(ValueError):DataPacket(struct.pack('<II',4097,0)+b'{}')
            broken=packet.raw[:-1]+b'\x01'
            with self.assertRaises(ValueError):DataPacket(broken)
            with self.assertRaises(ValueError):mapper.send('SOURCE_VEHICLE',{},b'x')
        finally:mapper.close();supervisor.close()

    def test_identity_uses_original_vehicle_bundle_and_keeps_s_incarnation_and_map_ids(self):
        profile,raw,policy,sample,bootstrap,peer,incarnation=fixture()
        policy.check_profile(profile)
        self.assertNotEqual(policy.calibration,raw.fields['calibration_bundle'])
        mapper,supervisor=self.links(peer)
        owner=IdentityOwner(peer,raw,policy,mapper_instance=incarnation)
        wire=encode(sample);map_id,frame_id=uid(),uid()
        metadata=proposal_metadata(raw_binding=raw,mapper_instance=incarnation,bootstrap=bootstrap,
            vehicle_wire=wire,policy=policy,nonce=uid(),map_epoch=map_id,frame_epoch=frame_id)
        try:
            mapper.send('SOURCE_IDENTITY',metadata,request_id=uid(),expires_us=2000);mapper.flush()
            proposal=supervisor.poll()[0]
            admitted=owner.accept(proposal,bootstrap=bootstrap,vehicle_wire=wire,vehicle_received_us=900,now_us=1100)
            self.assertEqual((admitted['map_epoch'],admitted['frame_epoch'],admitted['mapper_instance']),
                             (map_id,frame_id,incarnation))
            self.assertEqual(admitted['calibration'],sample.calibration)
            self.assertIsNone(admitted['scene_admission_ref'])
            self.assertEqual(owner.accept(proposal,bootstrap=bootstrap,vehicle_wire=wire,
                vehicle_received_us=900,now_us=1200),admitted)
            self.assertEqual(owner.revision,1)
            changed=dict(metadata,map_epoch=uid())
            mapper.send('SOURCE_IDENTITY',changed,request_id=proposal.header['request_id'],expires_us=2000);mapper.flush()
            with self.assertRaises(ValueError):owner.accept(supervisor.poll()[0],bootstrap=bootstrap,
                vehicle_wire=wire,vehicle_received_us=900,now_us=1200)
        finally:mapper.close();supervisor.close()

    def test_known_nonce_cannot_bypass_current_origin_or_renew_original_deadline(self):
        profile,raw,policy,sample,bootstrap,peer,incarnation=fixture()
        mapper,supervisor=self.links(peer);owner=IdentityOwner(peer,raw,policy,mapper_instance=incarnation)
        wire=encode(sample)
        metadata=proposal_metadata(raw_binding=raw,mapper_instance=incarnation,bootstrap=bootstrap,
            vehicle_wire=wire,policy=policy,nonce=uid(),map_epoch=uid(),frame_epoch=uid())
        try:
            request=uid();mapper.send('SOURCE_IDENTITY',metadata,request_id=request,expires_us=2000);mapper.flush()
            proposal=supervisor.poll()[0]
            owner.accept(proposal,bootstrap=bootstrap,vehicle_wire=wire,vehicle_received_us=900,now_us=1100)
            with self.assertRaises(ValueError):owner.accept(proposal,bootstrap=dict(bootstrap,database_uuid=uid()),
                vehicle_wire=wire,vehicle_received_us=900,now_us=1200)
            sample.gateway_instance=uid()
            with self.assertRaises(ValueError):owner.accept(proposal,bootstrap=bootstrap,vehicle_wire=wire,
                current_vehicle_wire=encode(sample),vehicle_received_us=900,now_us=1200)
            mapper.send('SOURCE_IDENTITY',metadata,request_id=request,expires_us=3000);mapper.flush()
            with self.assertRaises(ValueError):owner.accept(supervisor.poll()[0],bootstrap=bootstrap,
                vehicle_wire=wire,vehicle_received_us=900,now_us=1200)
        finally:mapper.close();supervisor.close()


if __name__=='__main__':unittest.main()
