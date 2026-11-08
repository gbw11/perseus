"""First pre-write evidence, original probe lifetime and real serial recovery."""
import errno
import os
import pty
import select
from uuid import UUID,uuid4

import pytest

from xenova_gateway import feedback
from xenova_gateway.feedback import FeedbackSerialSink,Framer,packet
from xenova_gateway.odometry import VehicleOdometry,OdometryConfig,OdometryError,PROBE,SAMPLE,ODOM_PROBE,ODOM_REPORT
from xenova_gateway.sink import SinkError


def config():
    return OdometryConfig('rc-test-1','encoder-lab-1',.001,.2,.25,2.,20000,200000,20,32,
                          .005,.01,.02,.02)


def owner():
    value=VehicleOdometry(config(),str(uuid4()));value.device_session(str(uuid4()))
    return value,str(uuid4())


def sample(value,pi,echo,index=1):
    return SAMPLE.pack(UUID(pi).bytes,UUID(value.device).bytes,echo,
                       7000000+(index-1)*100000,index,(index-1)*10,(index-1)*10,5,0,10)


def test_original_echo_wire_and_source_are_preserved_with_first_submission():
    value,pi=owner()
    for index,echo in enumerate((1000000,1100000),1):
        wire=value.probe(pi,echo)
        assert PROBE.unpack(wire)[2]==echo and value.pending[echo] is None
        assert value.mark_submitted(echo,echo+60000)
        result=value.receive(pi,sample(value,pi,echo,index),echo+62000)
    assert result['clock_error_us']==1000
    assert result['pi_sample_us']==1161000
    assert result['source_us']==7100000 and result['source_sample']==2
    assert result['odom_pose']['x']==pytest.approx(.01)
    conservative,pi2=owner();conservative.probe(pi2,1000000)
    with pytest.raises(OdometryError,match='ODOM_CLOCK_UNCERTAIN'):
        conservative.receive(pi2,sample(conservative,pi2,1000000),1062000)


def test_duplicate_mark_never_moves_first_bound_or_reference():
    value,pi=owner();value.probe(pi,1000000)
    assert value.mark_submitted(1000000,1020000)
    assert not value.mark_submitted(1000000,1020000)
    assert not value.mark_submitted(1000000,1030000)
    assert value.pending[1000000]==1020000
    value.receive(pi,sample(value,pi,1000000),1060000)
    assert value.previous[4]==1040000
    assert not value.pending


def test_zero_submission_is_distinct_from_unmarked():
    value,pi=owner();value.probe(pi,0)
    assert value.pending[0] is None
    assert value.mark_submitted(0,0) and value.pending[0]==0
    value.receive(pi,sample(value,pi,0),1000)
    assert value.previous[4]==500 and not value.errors


@pytest.mark.parametrize('echo,sent',[(True,1000000),(1000000,False),(1000000,None),
                                    (1000000,'1000000'),(1000000,999999),(1000000,2**64)])
def test_invalid_mark_input_never_fills_or_changes_pending(echo,sent):
    value,pi=owner();value.probe(pi,1000000)
    with pytest.raises(OdometryError,match='ODOM_PROBE_SUBMISSION'):
        value.mark_submitted(echo,sent)
    assert value.pending=={1000000:None}


def test_original_expiry_prevents_late_mark_and_reply_resurrection():
    value,pi=owner();value.probe(pi,1000000)
    with pytest.raises(OdometryError,match='ODOM_PROBE_EXPIRED'):
        value.mark_submitted(1000000,1200001)
    assert not value.pending and value.first_error=='ODOM_PROBE_EXPIRED'
    value.probe(pi,1300000);value.mark_submitted(1300000,1499000)
    with pytest.raises(OdometryError,match='ODOM_PROBE_EXPIRED'):
        value.receive(pi,sample(value,pi,1300000),1500001)
    assert not value.pending
    value.probe(pi,1600000);value.mark_submitted(1600000,1600000)
    value.receive(pi,sample(value,pi,1600000),1601000)
    value.probe(pi,1700000);value.mark_submitted(1700000,1700000)
    assert value.receive(pi,sample(value,pi,1700000,2),1701000)['source_sample']==2


def test_negative_rtt_session_reference_and_restart_are_rejected():
    value,pi=owner();value.probe(pi,1000000);value.mark_submitted(1000000,1020000)
    with pytest.raises(OdometryError,match='ODOM_PROBE_REFERENCE'):
        value.receive(pi,sample(value,pi,1000000),1019999)
    assert value.pending[1000000]==1020000
    with pytest.raises(OdometryError,match='ODOM_SESSION'):
        value.receive(str(uuid4()),sample(value,pi,1000000),1021000)
    value.device_session(str(uuid4()))
    with pytest.raises(OdometryError,match='ODOM_PROBE_REFERENCE'):
        value.mark_submitted(1000000,1021000)
    value.probe(pi,1100000);value.mark_submitted(1100000,1100000)
    assert value.receive(pi,sample(value,pi,1100000),1101000) is None


def test_true_post_submission_delay_still_fails_then_recovers():
    value,pi=owner();value.probe(pi,1000000);value.mark_submitted(1000000,1060000)
    with pytest.raises(OdometryError,match='ODOM_CLOCK_UNCERTAIN'):
        value.receive(pi,sample(value,pi,1000000),1101000)
    assert not value.pending and value.latest is None
    value.probe(pi,1200000);value.mark_submitted(1200000,1200000)
    value.receive(pi,sample(value,pi,1200000),1201000)
    value.probe(pi,1300000);value.mark_submitted(1300000,1300000)
    assert value.receive(pi,sample(value,pi,1300000,2),1301000)['clock_error_us']==500


@pytest.fixture
def serial_fixture():
    master,slave=pty.openpty();os.set_blocking(master,False)
    now=[1.];sensor,unused_pi=owner()
    sink=FeedbackSerialSink(os.ttyname(slave),clock=lambda:now[0],timeout_ms=500,odometry=sensor)
    sink.started=now[0];sink.ready=True;sink.device_session=sensor.device
    try:yield master,now,sensor,sink
    finally:sink.close();os.close(master);os.close(slave)


def read_probe(master):
    assert select.select([master],[],[],1)[0]
    rows=Framer().feed(os.read(master,4096))
    probes=[payload for kind,payload in rows if kind==ODOM_PROBE]
    assert len(probes)==1
    return PROBE.unpack(probes[0])


def test_actual_pty_packet_preparation_delay_uses_first_prewrite_time(monkeypatch,serial_fixture):
    master,now,sensor,sink=serial_fixture
    def slow_packet(kind,payload):
        wire=packet(kind,payload)
        if kind==ODOM_PROBE:now[0]+=.06
        return wire
    monkeypatch.setattr(feedback,'packet',slow_packet)
    sink.poll(now[0]);pi,device,echo=read_probe(master)
    assert echo==1000000 and sensor.pending[echo]==1060000
    now[0]=1.062
    os.write(master,packet(ODOM_REPORT,sample(sensor,sink.pi_session,echo)))
    sink.poll(now[0])
    assert sensor.previous[4]==1061000 and not sensor.errors
    assert sensor.previous[1]==7000000 and sink.ready and not sink.fault


def test_expired_preparation_has_zero_physical_send_and_next_poll_recovers(monkeypatch,serial_fixture):
    master,now,sensor,sink=serial_fixture
    def expired_packet(kind,payload):
        wire=packet(kind,payload)
        if kind==ODOM_PROBE:now[0]+=.200001
        return wire
    monkeypatch.setattr(feedback,'packet',expired_packet)
    sink.poll(now[0])
    assert sensor.first_error=='ODOM_PROBE_EXPIRED' and not sensor.pending
    assert not select.select([master],[],[],.01)[0]
    assert sink.ready and sink.fault is None
    monkeypatch.setattr(feedback,'packet',packet)
    sink.poll(now[0])
    for index in (1,2):
        if index==2:
            now[0]+=.099;sink.poll(now[0])
        pi,device,echo=read_probe(master)
        now[0]+=.001
        os.write(master,packet(ODOM_REPORT,sample(sensor,sink.pi_session,echo,index)))
        sink.poll(now[0])
    assert sink.snapshot['vehicle_sample']['source_sample']==2
    assert sink.snapshot['vehicle_sample']['odom_pose']['x']>0
    assert sensor.first_error=='ODOM_PROBE_EXPIRED' and sink.ready and sink.fault is None


def test_unexpected_submission_invariant_uses_existing_sink_failure_boundary(monkeypatch,serial_fixture):
    master,now,sensor,sink=serial_fixture
    original=OdometryError('ODOM_PROBE_REFERENCE')
    def invalid(echo,sent):raise original
    monkeypatch.setattr(sensor,'mark_submitted',invalid)
    with pytest.raises(SinkError,match='odometry submission reference invalid') as caught:
        sink.poll(now[0])
    assert caught.value.__cause__ is original
    assert not select.select([master],[],[],.01)[0]


def test_actual_pty_delay_after_mark_is_still_clock_uncertainty(monkeypatch,serial_fixture):
    master,now,sensor,sink=serial_fixture
    def slow_packet(kind,payload):
        wire=packet(kind,payload)
        if kind==ODOM_PROBE:now[0]+=.06
        return wire
    original_write=sink._write_bytes
    def delayed_write(wire):
        now[0]+=.05
        return original_write(wire)
    monkeypatch.setattr(feedback,'packet',slow_packet)
    monkeypatch.setattr(sink,'_write_bytes',delayed_write)
    sink.poll(now[0]);pi,device,echo=read_probe(master)
    assert echo==1000000 and sensor.pending[echo]==1060000
    now[0]=1.112
    os.write(master,packet(ODOM_REPORT,sample(sensor,sink.pi_session,echo)))
    sink.poll(now[0])
    assert sensor.first_error=='ODOM_CLOCK_UNCERTAIN'
    assert sink.rejected==1 and sensor.latest is None


@pytest.mark.parametrize('outcome',['partial','eio'])
def test_failed_or_partial_write_keeps_first_mark(monkeypatch,serial_fixture,outcome):
    master,now,sensor,sink=serial_fixture
    failure=OSError(errno.EIO,'original physical write failure')
    def write(data):
        if outcome=='eio':raise failure
        return len(data)-1
    monkeypatch.setattr(sink,'_write_bytes',write)
    if outcome=='eio':
        with pytest.raises(OSError) as caught:sink.poll(now[0])
        assert caught.value is failure
    else:
        with pytest.raises(SinkError,match='partial feedback-profile'):sink.poll(now[0])
    assert sensor.pending=={1000000:1000000}
    assert not sensor.mark_submitted(1000000,1001000)
    assert sensor.pending[1000000]==1000000
