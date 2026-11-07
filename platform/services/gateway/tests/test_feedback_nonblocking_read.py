"""Actual PTY tests for one bounded timeout=0 read without a FIONREAD query.

Existing test_feedback and test_feedback_failure_lifetime cover read-completion
stamps, owner HUP/cleanup and persistent first-failure fences; they remain used.
"""
import os
import select
from uuid import uuid4

import pytest

from xenova_gateway.feedback import (
    CAP_ACCEPT,CAP_APPLY,MAX_FRAME,FeedbackSerialSink,hello_ack,
)


@pytest.fixture
def serial_read(monkeypatch):
    master,slave=os.openpty();now=[0.]
    sink=FeedbackSerialSink(os.ttyname(slave),timeout_ms=500,clock=lambda:now[0])
    sizes=[];lengths=[];queries=[];read=sink._serial.read
    def forbidden_query(owner):
        queries.append(True)
        raise AssertionError('redundant in_waiting ioctl')
    monkeypatch.setattr(type(sink._serial),'in_waiting',property(forbidden_query))
    def observed(size):
        sizes.append(size);data=read(size);lengths.append(len(data));return data
    monkeypatch.setattr(sink._serial,'read',observed)
    def deliver(data):
        assert os.write(master,data)==len(data)
        assert select.select([sink._serial.fileno()],[],[],1)[0]
        before=len(sizes);sink.poll(now[0]);assert len(sizes)==before+1
    try:
        assert sink._serial.timeout==0
        yield sink,master,now,sizes,lengths,queries,deliver
    finally:sink.close();os.close(master);os.close(slave)


def test_idle_partial_and_joined_frames_use_one_bounded_read_and_no_in_waiting(serial_read):
    sink,master,now,sizes,lengths,queries,deliver=serial_read
    sink.poll(now[0])
    assert sizes==[4096] and lengths==[0] and sink.received==0 and sink.fault is None
    wire=hello_ack(sink.pi_session,str(uuid4()),CAP_ACCEPT|CAP_APPLY)
    cut=len(wire)//2
    now[0]=.1;deliver(wire[:cut])
    assert sink.received==0 and not sink.ready and sink.framer.buffer
    now[0]=.2;deliver(wire[cut:]+wire)
    assert sink.received==2 and sink.ready and sink.fault is None
    assert not sink.framer.buffer and not sink.framer.discard
    for _ in range(20):sink.poll(now[0])
    assert sizes==[4096]*23 and all(0<=length<=4096 for length in lengths)
    assert lengths[-20:]==[0]*20 and not queries


def test_oversize_and_invalid_frame_recover_to_normal_frame_in_same_read(serial_read):
    sink,master,now,sizes,lengths,queries,deliver=serial_read
    sink.poll(now[0])
    valid=hello_ack(sink.pi_session,str(uuid4()),CAP_ACCEPT|CAP_APPLY)
    invalid=bytearray(valid);invalid[-2]^=1
    deliver(b'x'*(MAX_FRAME+20)+b'\0'+bytes(invalid)+valid)
    assert sink.framer.oversize==1 and sink.framer.invalid==1
    assert sink.received==1 and sink.ready and sink.fault is None
    assert not sink.framer.discard and not sink.framer.buffer
    assert sizes==[4096,4096] and not queries


def test_read_cancel_keeps_waiting_bytes_for_next_poll(serial_read):
    sink,master,now,sizes,lengths,queries,deliver=serial_read
    sink.poll(now[0]);sink._serial.cancel_read()
    valid=hello_ack(sink.pi_session,str(uuid4()),CAP_ACCEPT|CAP_APPLY)
    deliver(valid)
    assert lengths[-1]==0 and sink.received==0 and not sink.ready
    assert select.select([sink._serial.fileno()],[],[],1)[0]
    sink.poll(now[0])
    assert sink.received==1 and sink.ready and sink.fault is None
    assert sizes==[4096]*3 and not queries


def test_idle_read_preserves_handshake_timeout_and_first_failure(serial_read):
    sink,master,now,sizes,lengths,queries,deliver=serial_read
    sink.poll(now[0]);now[0]=.499;sink.poll(now[0])
    assert not sink.ready and sink.fault is None
    now[0]=.5;sink.poll(now[0])
    assert sink.fault=='DEVICE_HANDSHAKE_TIMEOUT'
    deliver(hello_ack(sink.pi_session,str(uuid4()),CAP_ACCEPT|CAP_APPLY))
    assert sink.ready and sink.fault=='DEVICE_HANDSHAKE_TIMEOUT'
    assert sink.snapshot['accepted'] is None and sink.snapshot['applied'] is None
    assert sink.snapshot['physical_stop'] is None and not sink._sensor_available()
    assert sizes==[4096]*4 and not queries
