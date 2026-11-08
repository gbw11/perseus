"""실제 socketpair와 caller의 coherent B08 codec을 사용하는 byte peer 회귀다.

Gateway 권위·실물 firmware·운영 clock 수락을 대신하지 않는다. 테스트 실행 환경은
동결 B08 xenova_gateway.feedback/feedback3/scene_authority 패키지를 명시 제공해야 한다.
"""

import os
import socket
import time
import unittest

from test_simulation_motion_profile import motion_profile
from xenova_vehicle.simulation.feedback_peer import VirtualFeedbackPeer
from xenova_vehicle.simulation.plant import VirtualPlant


PI='10000000-0000-4000-8000-000000000001'
DEVICE='20000000-0000-4000-8000-000000000002'
EPOCH='30000000-0000-4000-8000-000000000003'
CLOCK_EPOCH='40000000-0000-4000-8000-000000000004'
CONFIG='1'*64
FIRMWARE='2'*64


class Clock:
    def __init__(self):self.now=3_000_000
    def __call__(self):return self.now
    def advance(self,delta=100):self.now+=delta;return self.now


def modules():
    from xenova_gateway import feedback, feedback3, scene_authority
    return feedback,feedback3,scene_authority


def protocol(motion,f3,sa):
    clock=motion.fields['clock']
    stationary=sa.StationaryPolicy(window_us=100_000,max_gap_us=40_000,max_age_us=200_000,
        metres_per_tick=motion.fields['encoder']['metres_per_tick'],max_wheel_mps=.01,max_yaw_rps=.01,
        unobserved_wheel_mps=.01,unobserved_yaw_rps=.01)
    return f3.Feedback3Profile(vehicle_config_sha256=CONFIG,firmware_sha256=FIRMWARE,
        timestamp_error_us=clock['timestamp_error_us'],drift_ppm=clock['drift_ppm'],
        max_offset_width_us=clock['max_offset_width_us'],lease_us=clock['lease_us'],probe_timeout_us=200_000,
        j_cutoff_us=10_000,j_apply_us=100,stationary=stationary,phase_budget_us=1_000_000,
        steering_tolerance_rad=.02,steering_settle_us=100_000)


class PeerRig:
    def __init__(self,steering_sensor_profile=None):
        from uuid import UUID
        self.UUID=UUID;self.fb,self.f3,self.sa=modules();self.clock=Clock()
        motion=motion_profile(timestamp_error_us=1,max_offset_width_us=50_000,
            steering_sensor_profile=steering_sensor_profile)
        self.plant=VirtualPlant(motion,initial_pose=(0.,0.,0.),epoch=EPOCH,clock=self.clock)
        self.a,self.b=socket.socketpair();self.a.setblocking(False);self.b.setblocking(False)
        self.policy=protocol(motion,self.f3,self.sa)
        self.peer=VirtualFeedbackPeer(self.b.fileno(),self.plant,feedback=self.fb,feedback3=self.f3,
            scene_authority=self.sa,protocol_profile=self.policy,configuration_sha256=CONFIG,
            firmware_sha256=FIRMWARE,device_session=DEVICE,
            capabilities=self.f3.REQUIRED_CAPS|0x400|(0 if steering_sensor_profile is None else self.fb.CAP_STEERING))
        self.framer=self.fb.Framer();self.pi=UUID(PI).bytes;self.device=UUID(DEVICE).bytes
        self.epoch=UUID(CLOCK_EPOCH).bytes;self.install=None

    def pump(self,steps=8,delta=100):
        rows=[]
        for _ in range(steps):
            self.clock.advance(delta);self.peer.poll_once()
            while True:
                try:raw=self.a.recv(4096)
                except BlockingIOError:break
                if not raw:break
                rows.extend(self.framer.frames(raw))
        return rows

    def turn(self,kind,payload):
        self.a.sendall(self.fb.packet(kind,payload))
        return self.pump()

    def hello(self):
        return self.turn(self.fb.HELLO,self.f3.HELLO3.pack(3,self.pi,bytes.fromhex(CONFIG)))

    def bind_clock(self):
        self.hello()
        last=None
        for seq in (1,2):
            p1=self.clock.now
            probe=self.f3.PROBE3.pack(self.pi,self.device,seq,p1)
            rows=self.turn(self.f3.CLOCK_PROBE,probe)
            raw=next(raw for kind,raw in rows if kind==self.f3.CLOCK_REPLY)
            last=self.f3.CLOCK3.unpack(raw)
        pi,device,seq,p1,d2,latch,d3,*_=last
        reference=self.clock.now
        pad=2*self.policy.timestamp_error_us+self.f3.drift_pad(self.policy.drift_ppm,reference-p1)
        lower,upper=p1-d2-pad,reference-d3+pad
        expires=p1+self.policy.lease_us
        cutoff=expires-upper-self.f3.drift_pad(self.policy.drift_ppm,expires-reference)-self.policy.j_cutoff_us
        self.install=(pi,device,self.epoch,1,seq,reference,lower,upper,expires,cutoff)
        payload=self.f3.INSTALL3.pack(*self.install)
        rows=self.turn(self.f3.CLOCK_INSTALL,payload)
        return payload,rows

    def renew_clock(self,probe_sequence,clock_sequence):
        p1=self.clock.now
        raw=self.f3.PROBE3.pack(self.pi,self.device,probe_sequence,p1)
        reply=next(body for kind,body in self.turn(self.f3.CLOCK_PROBE,raw) if kind==self.f3.CLOCK_REPLY)
        pi,device,seq,p1,d2,latch,d3,*_=self.f3.CLOCK3.unpack(reply)
        reference=self.clock.now
        pad=2*self.policy.timestamp_error_us+self.f3.drift_pad(self.policy.drift_ppm,reference-p1)
        lower,upper=p1-d2-pad,reference-d3+pad
        expires=p1+self.policy.lease_us
        cutoff=expires-upper-self.f3.drift_pad(self.policy.drift_ppm,expires-reference)-self.policy.j_cutoff_us
        payload=self.f3.INSTALL3.pack(pi,device,self.epoch,clock_sequence,seq,reference,lower,upper,expires,cutoff)
        return payload,self.turn(self.f3.CLOCK_INSTALL,payload)

    def stop(self,sequence=1,generation=1):
        return self.sa.COMMAND3.pack(self.pi,self.device,sequence,bytes(16),0,0,0,0,generation,0,2,0,0,0,0,0)

    def drive(self,sequence=2,velocity=400,steering=200,generation=1):
        issued=self.clock.now;expires=issued+80_000
        reference,upper=self.install[5],self.install[7]
        cutoff=min(self.install[9],expires-upper-self.f3.drift_pad(self.policy.drift_ppm,abs(expires-reference))-self.policy.j_cutoff_us)
        return self.sa.COMMAND3.pack(self.pi,self.device,sequence,self.epoch,1,issued,expires,cutoff,
            generation,1,1,1,velocity,steering,0,0)

    def close(self):
        self.peer.close();self.a.close();self.b.close()


@unittest.skipUnless(os.environ.get('XENOVA_TEST_PAIRED_GATEWAY') == '1',
    'NOT_SELECTED: explicit coherent Gateway/common requires XENOVA_TEST_PAIRED_GATEWAY=1')
class SimFeedbackPeerTests(unittest.TestCase):
    def setUp(self):
        if os.name!='posix':self.skipTest('explicit POSIX socketpair peer')
        self.rig=PeerRig();self.addCleanup(self.rig.close)

    def test_hello_uses_exact_hashes_caps_width_and_duplicate_preserves_original_binding(self):
        r=self.rig
        first=r.hello();ack=next(raw for kind,raw in first if kind==r.fb.HELLO_ACK)
        version,pi,device,caps,config,firmware,bits=r.f3.ACK3.unpack(ack)
        self.assertEqual((version,pi,device,bits),(3,r.pi,r.device,32))
        self.assertEqual(config.hex(),CONFIG);self.assertEqual(firmware.hex(),FIRMWARE)
        self.assertEqual(caps&r.fb.CAP_STEERING,0)
        producer=r.plant.feedback
        second=r.hello()
        self.assertEqual(next(raw for kind,raw in second if kind==r.fb.HELLO_ACK),ack)
        self.assertIs(r.plant.feedback,producer)

    def test_original_probe_install_and_duplicate_preserve_four_times_and_cutoff(self):
        r=self.rig;payload,rows=r.bind_clock()
        original=next(raw for kind,raw in rows if kind==r.f3.CLOCK_ACK)
        ack=r.f3.CLOCK_ACK3.unpack(original)
        self.assertEqual(ack[:5],r.install[:5]);self.assertEqual(ack[-1],r.install[-1])
        repeated=r.turn(r.f3.CLOCK_INSTALL,payload)
        self.assertEqual(next(raw for kind,raw in repeated if kind==r.f3.CLOCK_ACK),original)
        self.assertEqual(r.peer.snapshot()['clock_hwm'],1)
        p1=r.clock.now
        probe=r.f3.PROBE3.pack(r.pi,r.device,3,p1)
        reply=next(raw for kind,raw in r.turn(r.f3.CLOCK_PROBE,probe) if kind==r.f3.CLOCK_REPLY)
        c=r.f3.CLOCK3.unpack(reply)
        self.assertLessEqual(c[4],c[5]);self.assertLessEqual(c[5],c[6])
        self.assertEqual(c[10],5);self.assertGreater(c[12],0)
        repeated=r.turn(r.f3.CLOCK_PROBE,probe)
        self.assertEqual(next(raw for kind,raw in repeated if kind==r.f3.CLOCK_REPLY),reply)
        self.assertEqual(r.peer.snapshot()['duplicate_probes'],1)

    def test_clock_evidence_is_original_immutable_two_entry_snapshot_and_duplicate_does_not_retime(self):
        r=self.rig;payload,rows=r.bind_clock()
        original=r.peer.clock_evidence_snapshot()
        self.assertIs(type(original),tuple);self.assertEqual(len(original),1)
        record=original[0]
        self.assertEqual(record.install_payload,payload)
        kind,ack=r.fb.unpack(record.peer_produced_ack_wire)
        self.assertEqual(kind,r.f3.CLOCK_ACK)
        self.assertEqual(ack,next(raw for k,raw in rows if k==r.f3.CLOCK_ACK))
        with self.assertRaises(AttributeError):record.install_payload=b''
        with self.assertRaises(ValueError):type(record)(bytearray(payload),record.peer_produced_ack_wire)
        r.turn(r.f3.CLOCK_INSTALL,payload)
        self.assertIs(r.peer.clock_evidence_snapshot(),original)
        second,_=r.renew_clock(3,2)
        middle=r.peer.clock_evidence_snapshot()
        self.assertEqual(tuple(r.f3.INSTALL3.unpack(item.install_payload)[3] for item in middle),(1,2))
        third,_=r.renew_clock(4,3)
        latest=r.peer.clock_evidence_snapshot()
        self.assertEqual(tuple(r.f3.INSTALL3.unpack(item.install_payload)[3] for item in latest),(2,3))
        self.assertEqual((latest[0].install_payload,latest[1].install_payload),(second,third))
        self.assertEqual(original,(record,));self.assertEqual(record.install_payload,payload)

    def test_actual_apply_reports_measurements_not_command_targets_and_wheels_diverge(self):
        r=self.rig;r.bind_clock();r.turn(r.f3.COMMAND,r.stop())
        command=r.drive();rows=r.turn(r.f3.COMMAND,command)
        reports=[r.sa.FEEDBACK3.unpack(raw) for kind,raw in rows if kind==r.f3.REPORT]
        applied=next(row for row in reports if row[2]==2 and row[6]==2)
        self.assertEqual(applied[10],5);self.assertEqual(applied[12],0)
        self.assertNotEqual(applied[11],400)
        self.assertAlmostEqual(r.plant.snapshot().target_steering_rad,.2)
        more=r.pump(steps=5,delta=10_000)
        sensors=[r.f3.SENSOR3.unpack(raw) for kind,raw in more if kind==r.f3.SENSOR]
        self.assertTrue(sensors)
        last=sensors[-1]
        self.assertGreater(last[5],last[4]);self.assertEqual(last[6],5);self.assertGreater(last[8],0)
        self.assertGreater(r.plant.snapshot().velocity_mps,0.)
        self.assertLess(r.plant.snapshot().velocity_mps,.4)

    def test_explicit_measured_sensor_preserves_original_quality5_and_emits_same_latch_proof(self):
        import json
        from xenova_vehicle.simulation.steering_sensor import VirtualSteeringSensorProfile
        leaf=VirtualSteeringSensorProfile(sensor_id='development-servo-1',quantization_rad=.001,
            angle_error_rad=.0005,timestamp_error_us=1,max_rate_rps=5.)
        r=PeerRig(leaf);self.addCleanup(r.close)
        legacy=motion_profile(timestamp_error_us=1,max_offset_width_us=50_000)
        self.assertNotEqual(r.plant.profile.sha256,legacy.sha256)
        r.bind_clock();r.turn(r.f3.COMMAND,r.stop())
        rows=r.turn(r.f3.COMMAND,r.drive())
        applied=next(r.sa.FEEDBACK3.unpack(raw) for kind,raw in rows
            if kind==r.f3.REPORT and r.sa.FEEDBACK3.unpack(raw)[2]==2 and r.sa.FEEDBACK3.unpack(raw)[6]==2)
        self.assertEqual(applied[10],7)
        self.assertNotEqual(applied[12],200)
        rows=r.pump(steps=5,delta=10_000)
        sensors=[r.f3.SENSOR3.unpack(raw) for kind,raw in rows if kind==r.f3.SENSOR]
        self.assertTrue(sensors);self.assertTrue(all(row[6]==3 and row[8]>0 for row in sensors))
        self.assertEqual(r.plant.feedback.last_sample.quality,5)
        records,omitted=r.peer.evidence_snapshot()
        self.assertEqual(omitted,0)
        clock_records=[record for record in records if record.kind==r.f3.CLOCK_REPLY]
        self.assertTrue(clock_records)
        for record in clock_records:
            kind,payload=r.fb.unpack(record.wire)
            self.assertEqual(r.f3.CLOCK3.unpack(payload)[10],3)
        for record in records:
            provenance=json.loads(record.steering_provenance)
            self.assertEqual(provenance['quality'],3)
            self.assertEqual(provenance['wheel']['original_quality'],5)
            measurement=provenance['measurement']
            self.assertEqual(measurement['source_kind'],'SIMULATED_MEASURED_STEERING')
            self.assertEqual(int(measurement['source_sample']),record.source_sample)
            self.assertEqual(int(measurement['source_us']),record.source_us)
            self.assertFalse(provenance['physical_qualified'])

    def test_duplicate_command_replays_original_reports_without_reapply_and_conflict_fences(self):
        r=self.rig;r.bind_clock();r.turn(r.f3.COMMAND,r.stop())
        command=r.drive();first=r.turn(r.f3.COMMAND,command)
        count=r.peer.snapshot()['applied']
        repeated=r.turn(r.f3.COMMAND,command)
        def originals(rows):
            return [raw for kind,raw in rows if kind==r.f3.REPORT and r.sa.FEEDBACK3.unpack(raw)[2]==2]
        self.assertEqual(originals(repeated),originals(first))
        self.assertEqual(r.peer.snapshot()['applied'],count)
        fields=list(r.sa.COMMAND3.unpack(command));fields[12]=300
        r.a.sendall(r.fb.packet(r.f3.COMMAND,r.sa.COMMAND3.pack(*fields)))
        with self.assertRaisesRegex(ValueError,'same command'):r.pump(steps=1)
        self.assertEqual(r.plant.snapshot().target_velocity_mps,0.)
        self.assertEqual(r.peer.snapshot()['applied'],count)
        self.assertTrue(r.peer.snapshot()['link_lost'])
        self.assertTrue(r.plant.snapshot().valid)

    def test_eof_stops_target_but_actual_braking_and_encoder_continue_before_simulation_close(self):
        r=self.rig;r.bind_clock();r.turn(r.f3.COMMAND,r.stop())
        r.turn(r.f3.COMMAND,r.drive())
        r.pump(steps=4,delta=10_000)
        before=r.plant.snapshot();ticks=r.plant.feedback.ticks
        self.assertGreater(before.velocity_mps,0.)
        r.a.close();r.clock.advance(1000)
        with self.assertRaises(EOFError):r.peer.poll_once()
        self.assertEqual(r.plant.snapshot().target_velocity_mps,0.)
        self.assertGreater(r.plant.snapshot().velocity_mps,0.)
        self.assertTrue(r.plant.snapshot().valid)
        for _ in range(2):r.clock.advance(10_000);r.peer.poll_once()
        after=r.plant.snapshot()
        self.assertLess(after.velocity_mps,before.velocity_mps)
        self.assertGreater(r.plant.feedback.ticks[0],ticks[0])
        self.assertFalse(r.peer.snapshot()['simulation_stopped'])
        r.peer.close()
        self.assertTrue(r.peer.snapshot()['simulation_stopped'])
        self.assertFalse(r.plant.snapshot().valid)

    def test_expiry_stops_original_target_and_late_duplicate_does_not_renew_or_apply(self):
        r=self.rig;r.bind_clock();r.turn(r.f3.COMMAND,r.stop())
        command=r.drive();r.turn(r.f3.COMMAND,command)
        cutoff=r.sa.COMMAND3.unpack(command)[7]
        while r.clock.now<cutoff:r.pump(steps=1,delta=min(5000,cutoff-r.clock.now))
        self.assertEqual(r.plant.snapshot().target_velocity_mps,0.)
        self.assertTrue(r.plant.snapshot().valid)
        before=r.peer.snapshot()['applied'];r.turn(r.f3.COMMAND,command)
        self.assertEqual(r.peer.snapshot()['applied'],before)
        self.assertEqual(r.plant.snapshot().target_velocity_mps,0.)
        self.assertEqual(r.peer.snapshot()['clock_hwm'],1)

    def test_clockless_stop_and_old_generation_stop_do_not_reset_generation_or_rearm(self):
        r=self.rig;r.hello()
        first=r.turn(r.f3.COMMAND,r.stop(generation=9))
        self.assertTrue(any(kind==r.f3.REPORT and r.sa.FEEDBACK3.unpack(raw)[6]==2 for kind,raw in first))
        r.turn(r.f3.COMMAND,r.stop(sequence=2,generation=3))
        self.assertEqual(r.peer.snapshot()['control_generation'],9)
        self.assertEqual(r.peer.snapshot()['clock_hwm'],0)
        self.assertEqual(r.plant.snapshot().target_velocity_mps,0.)

    def test_peer_closes_only_its_dup_and_non_daemon_thread_eof_stops_without_rearm(self):
        import fcntl
        r=self.rig;fd=r.b.fileno();flags=fcntl.fcntl(fd,fcntl.F_GETFL)
        r.peer.close()
        self.assertEqual(fcntl.fcntl(fd,fcntl.F_GETFL),flags)
        os.fstat(fd)
        fb,f3,sa=modules();motion=motion_profile(timestamp_error_us=1,max_offset_width_us=50_000)
        plant=VirtualPlant(motion,initial_pose=(0.,0.,0.),epoch=EPOCH)
        a,b=socket.socketpair();a.setblocking(False);b.setblocking(False)
        peer=VirtualFeedbackPeer(b.fileno(),plant,feedback=fb,feedback3=f3,scene_authority=sa,
            protocol_profile=protocol(motion,f3,sa),configuration_sha256=CONFIG,firmware_sha256=FIRMWARE,
            device_session=DEVICE,capabilities=f3.REQUIRED_CAPS|0x400)
        try:
            peer.start();a.close()
            until=time.monotonic()+1
            while not peer.snapshot()['closed'] and time.monotonic()<until:time.sleep(.001)
            peer.close()
            self.assertTrue(peer.snapshot()['closed']);self.assertFalse(peer.snapshot()['thread_alive'])
            self.assertEqual(plant.snapshot().target_velocity_mps,0.)
            self.assertFalse(plant.snapshot().valid);os.fstat(b.fileno())
        finally:peer.close();a.close();b.close()
