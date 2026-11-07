"""Actual PTY owner RX: one native read, unchanged direct/URL public fallback."""
import errno
import os
import select
import socket
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

import xenova_gateway.feedback as feedback_module
from xenova_gateway.feedback import FeedbackSerialSink,Framer,MAX_FRAME,hello_ack,parse_command
from xenova_gateway.frame import StopFrame
from xenova_gateway.output_owner import OutputOwner
from xenova_gateway.sink import SinkError


@pytest.fixture
def owned_port():
    master,slave=os.openpty();now=[0.]
    sink=FeedbackSerialSink(os.ttyname(slave),timeout_ms=500,clock=lambda:now[0])
    try:
        assert sink._owned_read_fd==sink._write_fd and not os.get_blocking(sink._owned_read_fd)
        yield sink,master,now
    finally:
        sink.close();os.close(master);os.close(slave)


def _readable(sink):
    assert select.select([sink._owned_read_fd],[],[],1)[0]


def test_native_one_read_no_pyserial_select_and_original_completion_stamp(owned_port,monkeypatch):
    sink,master,now=owned_port;calls=[];read=os.read
    def one(fd,size):
        calls.append((fd,size));value=read(fd,size);now[0]=.125;return value
    monkeypatch.setattr(feedback_module,'os',SimpleNamespace(read=one,name=os.name))
    def forbidden(*args):raise AssertionError('owned RX must not re-enter pyserial read/select')
    monkeypatch.setattr(sink._serial,'read',forbidden)
    import serial.serialposix
    monkeypatch.setattr(serial.serialposix,'select',SimpleNamespace(select=forbidden))
    # Actual idle VMIN=0 read is empty, and not a link failure without readiness.
    sink._poll_owned(now[0]);assert len(calls)==1 and sink.fault is None
    wire=hello_ack(sink.pi_session,str(uuid4()));cut=len(wire)//2
    os.write(master,wire[:cut]);_readable(sink);sink._poll_owned(now[0],read_ready=True)
    assert len(calls)==2 and sink.received==0 and sink.framer.buffer
    observed=[];receive=sink._receive
    def recorded(kind,payload,at):
        observed.append(at);now[0]+=.05;return receive(kind,payload,at)
    monkeypatch.setattr(sink,'_receive',recorded)
    os.write(master,wire[cut:]+wire);_readable(sink);sink._poll_owned(now[0],read_ready=True)
    assert len(calls)==3 and sink.received==2 and sink.ready
    assert observed==[.125,.125]
    assert all(fd==sink._owned_read_fd and size==4096 for fd,size in calls)
    valid=hello_ack(sink.pi_session,sink.device_session)
    os.write(master,b'x'*(MAX_FRAME+20)+b'\0'+valid);_readable(sink)
    sink._poll_owned(now[0],read_ready=True)
    assert sink.framer.oversize==1 and sink.received==3 and sink.fault is None


@pytest.mark.parametrize('code',[errno.EAGAIN,errno.EWOULDBLOCK,errno.EALREADY,errno.EINPROGRESS,errno.EINTR])
def test_transient_native_read_is_one_attempt_then_normal_recovery(owned_port,monkeypatch,code):
    sink,master,now=owned_port;calls=[];read=os.read
    def failed(fd,size):
        calls.append(size)
        if len(calls)==1:raise OSError(code,'trial transient read')
        return read(fd,size)
    monkeypatch.setattr(feedback_module,'os',SimpleNamespace(read=failed,name=os.name))
    sink._poll_owned(now[0],read_ready=True)
    assert calls==[4096] and sink.fault is None and sink.received==0
    os.write(master,hello_ack(sink.pi_session,str(uuid4())));_readable(sink)
    sink._poll_owned(now[0],read_ready=True)
    assert calls==[4096,4096] and sink.ready and sink.fault is None


def test_native_original_oserror_is_not_repackaged_or_cleared(owned_port,monkeypatch):
    sink,master,now=owned_port;first=OSError(errno.EIO,'original read failure')
    def failed(fd,size):raise first
    monkeypatch.setattr(feedback_module,'os',SimpleNamespace(read=failed,name=os.name))
    with pytest.raises(OSError) as caught:sink._poll_owned(now[0],read_ready=True)
    assert caught.value is first and caught.value.errno==errno.EIO


def test_idle_empty_and_ready_empty_are_distinct_and_hint_is_not_renewed(owned_port,monkeypatch):
    sink,master,now=owned_port;reads=[]
    def empty(fd,size):reads.append(size);return b''
    monkeypatch.setattr(feedback_module,'os',SimpleNamespace(read=empty,name=os.name))
    sink._poll_owned(now[0])
    assert reads==[4096] and sink.fault is None
    with pytest.raises(SinkError,match='EOF'):sink._poll_owned(now[0],read_ready=True)
    sink._poll_owned(now[0])
    assert reads==[4096]*3 and sink.fault is None


def test_owner_wait_has_single_use_readable_hint_idle_no_spin_stop_and_close(owned_port,monkeypatch):
    sink,master,now=owned_port;reads=[];waits=[];read=sink._read_owned
    initial=threading.Event();receive=threading.Event();after_ready_idle=threading.Event()
    def measured(ready):
        reads.append(ready)
        value=read(ready)
        initial.set()
        if ready:receive.set()
        elif receive.is_set():after_ready_idle.set()
        return value
    monkeypatch.setattr(sink,'_read_owned',measured)
    def no_public_read(size):raise AssertionError('owner native path used public read')
    monkeypatch.setattr(sink._serial,'read',no_public_read)
    wait=OutputOwner._wait_io
    def waited(owner,delay):
        waits.append(delay);return wait(owner,delay)
    monkeypatch.setattr(OutputOwner,'_wait_io',waited)
    owner=OutputOwner(sink,command_timeout_s=.1,clock=lambda:now[0])
    try:
        assert initial.wait(1)
        os.write(master,hello_ack(sink.pi_session,str(uuid4())))
        assert receive.wait(1) and after_ready_idle.wait(1)
        assert sink.ready and owner.first_failure is None
        # One initial poll and at most one poll per existing wait. No second
        # readiness call/read loop exists inside the owned receive helper.
        assert len(reads)<=len(waits)+1 and all(delay>0 for delay in waits)
        submission=owner.submit_stop(StopFrame(42,0,1),now[0])
        assert submission.event.wait(1)
        receipt=submission.outcome();assert isinstance(receipt.frame,StopFrame)
        assert parse_command(bytes.fromhex(receipt.metadata['wire_hex']))[-1]==receipt.frame
        assert reads[0] is False and True in reads and reads[-1] is False
        fds=owner._wake_read,owner._wake_write
    finally:owner.close()
    assert not owner.thread.is_alive() and not sink._serial.is_open
    for fd in fds:
        with pytest.raises(OSError):os.fstat(fd)


def test_actual_hup_is_confirmed_then_owner_uses_bounded_timed_failure_checks(monkeypatch):
    master,slave=os.openpty();sink=FeedbackSerialSink(os.ttyname(slave),timeout_ms=500)
    owner=None
    try:
        # Avoid a failed initial HELLO write masking the RX EOF observation.
        sink.started=time.monotonic();owner=OutputOwner(sink,command_timeout_s=.1)
        os.close(master);master=None
        deadline=time.monotonic()+1
        while owner.first_failure is None and time.monotonic()<deadline:time.sleep(.001)
        assert isinstance(owner.first_failure,SinkError) and 'EOF' in str(owner.first_failure)
        assert owner._read_fd is None and owner.device_view()['fault']=='DEVICE_LINK_UNAVAILABLE'
        first=owner.first_failure
        time.sleep(.03)
        assert owner.first_failure is first and owner.thread.is_alive()
    finally:
        if owner is not None:owner.close()
        else:sink.close()
        if master is not None:os.close(master)
        os.close(slave)


def test_socket_url_even_with_socket_fd_keeps_public_read_fallback(monkeypatch):
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1)
    held=[];accepted=threading.Event();release=threading.Event()
    def peer():
        connection,_=listener.accept();held.append(connection);accepted.set();release.wait(2);connection.close()
    thread=threading.Thread(target=peer);thread.start();sink=None
    try:
        sink=FeedbackSerialSink('socket://127.0.0.1:'+str(listener.getsockname()[1]),timeout_ms=500,clock=lambda:0.)
        assert accepted.wait(1) and sink._owned_read_fd is None
        calls=[];read=sink._serial.read
        def public(size):calls.append(size);return read(size)
        monkeypatch.setattr(sink._serial,'read',public)
        def forbidden(ready):raise AssertionError('URL cannot use native tty read')
        monkeypatch.setattr(sink,'_read_owned',forbidden)
        sink._poll_owned(0.,read_ready=True)
        assert calls==[4096] and sink.fault is None
    finally:
        if sink is not None:sink.close()
        release.set();listener.close();thread.join(2)
    assert not thread.is_alive()


def test_supported_backend_without_fd_keeps_public_read_fallback(monkeypatch):
    # Explicit boundary fixture: the existing SerialSink supports a backend
    # whose fileno is absent/NotImplemented; this is not a new URL protocol.
    import serial
    class Backend:
        def fileno(self):raise NotImplementedError
        def read(self,size):return b''
        def write(self,data):return len(data)
        def close(self):pass
    backend=Backend()
    monkeypatch.setattr(serial,'serial_for_url',lambda *args,**kwargs:backend)
    sink=FeedbackSerialSink('fixture-no-fd://',timeout_ms=500,clock=lambda:0.)
    try:
        assert sink._write_fd is None and sink._owned_read_fd is None
        calls=[];read=sink._serial.read
        def public(size):calls.append(size);return read(size)
        monkeypatch.setattr(sink._serial,'read',public)
        sink._poll_owned(0.,read_ready=True)
        assert calls==[4096] and sink.fault is None
    finally:sink.close()
