"""feedback-2 provider: real codec/PTY, encoder integration and finite time bounds."""
from dataclasses import replace
import os
import pty
import time
from uuid import UUID,uuid4

import pytest

from xenova_gateway.odometry import VehicleOdometry,OdometryConfig,OdometryError,PROBE,SAMPLE,ODOM_PROBE,ODOM_REPORT,CAP_ODOMETRY
from xenova_gateway.feedback import FeedbackSerialSink,Framer,HELLO,HELLO_PAYLOAD,hello_ack,packet,CAP_ACCEPT,CAP_APPLY

def config():
    # Explicit kinematic/sensor test fixture. No measured vehicle guarantee.
    return OdometryConfig('rc-test-1','encoder-lab-1',.001,.2,.25,2.,20000,200000,20,32,
                          .005,.01,.02,.02)

def test_independent_feedback2_vectors_and_real_cumulative_integration():
    import json
    from pathlib import Path
    vector=json.loads((Path(__file__).resolve().parents[4]/'docs/contracts/vectors/gateway-feedback-2.json').read_text())
    pi=vector['pi_session'];owner=VehicleOdometry(config(),str(uuid4()));owner.device_session(vector['device_session'])
    for row in vector['frames']:
        payload=bytes.fromhex(row['payload_hex']);wire=bytes.fromhex(row['wire_hex'])
        assert Framer().feed(wire)==[(row['id'],payload)]
        assert packet(row['id'],payload)==wire
        if row['name']=='probe':assert owner.probe(pi,1000000)==payload
        else:
            if row['name']=='sample2':owner.probe(pi,1100000)
            value=owner.receive(pi,payload,1001000 if row['name']=='sample1' else 1101000)
            if row['name']=='sample1':assert value is None
            else:assert value['odom_pose']['x']==pytest.approx(.1) and value['velocity_mps']==pytest.approx(1.)

def observe(owner,pi,at,source,sample,left,right,*,quality=1,delay=1000,steer=0,error=0):
    payload=owner.probe(pi,at)
    assert payload is not None
    return owner.receive(pi,SAMPLE.pack(UUID(pi).bytes,UUID(owner.device).bytes,at,source,sample,left,right,quality,steer,error),at+delay)

def test_real_cumulative_encoder_geometry_clock_and_unknown_measurements():
    pi=str(uuid4());owner=VehicleOdometry(config(),str(uuid4()));owner.device_session(str(uuid4()))
    assert observe(owner,pi,1000000,1000000,1,0,0) is None
    straight=observe(owner,pi,1100000,1100000,2,100,100)
    assert straight['odom_pose']['x']==pytest.approx(.1) and straight['velocity_mps']==pytest.approx(1.)
    turn=observe(owner,pi,1200000,1200000,3,180,200,quality=5,steer=138,error=10)
    assert turn['odom_pose']['y']>0 and turn['odom_pose']['yaw']==pytest.approx(.1)
    assert 'measured_steering_rad' not in turn and turn['estimated_steering_rad']>0
    assert turn['position_error_m']>0 and turn['clock_error_us']==500
    assert owner.current(1500000) is None
    epoch=owner.epoch
    assert observe(owner,pi,1500000,1500000,4,180,200) is None and owner.epoch!=epoch
    stopped=observe(owner,pi,1600000,1600000,5,180,200,quality=5,steer=0,error=10)
    assert stopped['velocity_mps']==0 and stopped['estimated_steering_rad']==0

def test_reset_wrap_reorder_bad_quality_and_normal_recovery():
    pi=str(uuid4());owner=VehicleOdometry(config(),str(uuid4()));owner.device_session(str(uuid4()))
    observe(owner,pi,1000000,1000000,1,2**31-5,2**31-5)
    wrapped=observe(owner,pi,1100000,1100000,2,-2**31+5,-2**31+5)
    assert wrapped['odom_pose']['x']==pytest.approx(.01)
    with pytest.raises(OdometryError,match='ODOM_SENSOR_QUALITY'):
        observe(owner,pi,1200000,1200000,3,0,0,quality=0)
    assert owner.current(1201000) is None
    old_epoch=owner.epoch;owner.device_session(str(uuid4()))
    assert owner.epoch!=old_epoch and owner.previous is None and owner.current(1201000) is None
    observe(owner,pi,1300000,1300000,1,0,0)
    assert observe(owner,pi,1400000,1400000,2,20,20)['odom_pose']['x']==pytest.approx(.02)

def test_slow_probe_rejected_without_clock_subtraction():
    pi=str(uuid4());owner=VehicleOdometry(config(),str(uuid4()));owner.device_session(str(uuid4()))
    with pytest.raises(OdometryError,match='ODOM_CLOCK_UNCERTAIN'):
        observe(owner,pi,1000000,9000000000,1,0,0,delay=50000)
    assert owner.first_error=='ODOM_CLOCK_UNCERTAIN'
    observe(owner,pi,1100000,9000100000,2,0,0)
    assert observe(owner,pi,1200000,9000200000,3,20,20)['velocity_mps']==pytest.approx(.2)

def test_feedback2_actual_pty_shares_single_serial_owner_and_recovers_frames():
    master,slave=pty.openpty();os.set_blocking(master,False)
    owner=VehicleOdometry(config(),str(uuid4()))
    sink=FeedbackSerialSink(os.ttyname(slave),timeout_ms=500,odometry=owner)
    frames=Framer();device=str(uuid4());sample=0
    try:
        deadline=time.monotonic()+2
        while time.monotonic()<deadline and sample<3:
            now=time.monotonic();sink.poll(now)
            try:data=os.read(master,4096)
            except BlockingIOError:data=b''
            for kind,payload in frames.feed(data):
                if kind==HELLO:
                    version,pi=HELLO_PAYLOAD.unpack(payload)
                    assert version==2
                    os.write(master,hello_ack(str(UUID(bytes=pi)),device,CAP_ACCEPT|CAP_APPLY|CAP_ODOMETRY,version=2))
                elif kind==ODOM_PROBE:
                    pi,dev,echo=PROBE.unpack(payload);sample+=1
                    wire=packet(ODOM_REPORT,SAMPLE.pack(pi,dev,echo,sample*20000,sample,sample,sample,5,0,10))
                    # A partial frame must not become a partially applied pose.
                    os.write(master,wire[:5]);sink.poll(time.monotonic())
                    os.write(master,wire[5:])
            time.sleep(.002)
        sink.poll(time.monotonic())
        value=sink._view()['vehicle_sample']
        assert value and value['source_sample']==3 and value['odom_pose']['x']>0
        assert sink.ready and not sink.fault
    finally:sink.close();os.close(master);os.close(slave)
