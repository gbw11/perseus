"""Single-owner Paho external loop, bounded QoS0 backlog and real recovery.

Faults affect socket acceptance only. The real fixed Paho packet builder,
writer and local broker still carry the original binary clock messages.
"""
import errno
import socket
import threading
import time
from uuid import uuid4

import paho.mqtt.client as mqtt
import pytest

from xenova_drive_world import PreparedClock,decode,schema
from xenova_gateway import drive_link
from xenova_gateway.drive_link import DriveLinkIO,inbound
from xenova_mqtt.client import _new_paho
from test_drive_clock_send_boundary import reply
from test_state_transport import broker,until
from test_verified import A,B


class ExternalClient:
    """Public external-loop API with selectable real sockets and result faults."""
    def __init__(self,sock,*,writing=False,read_rc=0,write_rc=0):
        self.sock=sock;self.writing=writing;self.read_rc=read_rc;self.write_rc=write_rc;self.calls=[]

    def socket(self):return self.sock
    def want_write(self):return self.writing
    def loop_read(self):self.calls.append('read');return self.read_rc
    def loop_write(self):self.calls.append('write');return self.write_rc
    def loop_misc(self):self.calls.append('misc');return mqtt.MQTT_ERR_SUCCESS


def test_empty_and_interrupted_wait_keep_misc_and_the_same_owner(monkeypatch):
    io=DriveLinkIO('127.0.0.1',1,None,None,(A,B))
    unused,peer=socket.socketpair()
    try:
        # A script avoids elapsed-time assumptions for the idle and EINTR paths.
        client=ExternalClient(unused);waits=[]
        def interrupted(read,write,error,timeout):
            waits.append((read,write,error,timeout));raise InterruptedError
        monkeypatch.setattr(drive_link.select,'select',interrupted)
        assert io._network_once(client,mqtt)==mqtt.MQTT_ERR_SUCCESS
        monkeypatch.setattr(drive_link.select,'select',lambda *args:([],[],[]))
        assert io._network_once(client,mqtt)==mqtt.MQTT_ERR_SUCCESS
        assert client.calls==['misc','misc']
        assert waits==[([unused],[],[],.002)]
    finally:unused.close();peer.close()


@pytest.mark.parametrize('read_rc,write_rc,expected,calls',[
    (mqtt.MQTT_ERR_SUCCESS,mqtt.MQTT_ERR_SUCCESS,mqtt.MQTT_ERR_SUCCESS,['read','write','misc']),
    (mqtt.MQTT_ERR_AGAIN,mqtt.MQTT_ERR_AGAIN,mqtt.MQTT_ERR_SUCCESS,['read','write','misc']),
    (mqtt.MQTT_ERR_CONN_LOST,mqtt.MQTT_ERR_SUCCESS,mqtt.MQTT_ERR_CONN_LOST,['read']),
    (mqtt.MQTT_ERR_SUCCESS,mqtt.MQTT_ERR_CONN_LOST,mqtt.MQTT_ERR_CONN_LOST,['read','write']),
])
def test_read_write_error_results_are_not_hidden(read_rc,write_rc,expected,calls):
    io=DriveLinkIO('127.0.0.1',1,None,None,(A,B))
    left,right=socket.socketpair()
    try:
        right.send(b'x');client=ExternalClient(left,writing=True,read_rc=read_rc,write_rc=write_rc)
        assert io._network_once(client,mqtt)==expected
        assert client.calls==calls
    finally:left.close();right.close()


def test_select_failure_preserves_first_cause_and_a_later_call_recovers(monkeypatch):
    io=DriveLinkIO('127.0.0.1',1,None,None,(A,B));client=ExternalClient(object())
    first=OSError(errno.EBADF,'closed socket')
    def fail(*args):raise first
    monkeypatch.setattr(drive_link.select,'select',fail)
    assert io._network_once(client,mqtt)==mqtt.MQTT_ERR_CONN_LOST
    assert io.first_transport is first
    monkeypatch.setattr(drive_link.select,'select',lambda *args:([],[],[]))
    assert io._network_once(client,mqtt)==mqtt.MQTT_ERR_SUCCESS
    assert io.first_transport is first and client.calls==['misc']
    client.sock=None
    assert io._network_once(client,mqtt)==mqtt.MQTT_ERR_NO_CONN


def test_actual_paho_max_queued_messages_does_not_bound_old_qos0_offer_pattern(monkeypatch):
    """Reproduce the old unconditional pop/publish pattern against real Paho."""
    client=_new_paho('qos0-window-'+str(uuid4()));client.max_queued_messages_set(2)
    left,right=socket.socketpair();client._sock=left
    def blocked(buf):raise BlockingIOError(errno.EAGAIN,'synthetic slow peer')
    monkeypatch.setattr(client,'_sock_send',blocked)
    try:
        for n in range(64):assert client.publish(inbound(A),str(n).encode(),qos=0).rc==mqtt.MQTT_ERR_SUCCESS
        # These are implementation observations in a regression test, never
        # product manipulation of Paho's private queue or wake sockets.
        assert client.want_write() and len(client._out_packet)==64>2
    finally:client._sock=None;left.close();right.close()


@pytest.mark.parametrize('release',[True,False])
def test_actual_owner_keeps_one_qos0_packet_and_latest_slot_during_slow_peer(broker,monkeypatch,release):
    received=[];subscribed=threading.Event();reader=_new_paho('bounded-reader-'+str(uuid4()))
    reader.on_connect=lambda c,u,f,r,*extra:c.subscribe(inbound(A),qos=0)
    reader.on_subscribe=lambda *args:subscribed.set()
    reader.on_message=lambda c,u,m:received.append(decode(bytes(m.payload),schema.DriveLink))
    reader.connect('127.0.0.1',broker,10);reader.loop_start()
    blocked=threading.Event();partial=threading.Event();owners=[];send_sizes=[];published_depths=[]
    original=mqtt.Client
    class SlowClient(original):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs);owners.append(self)
        def _sock_send(self,buf):
            if blocked.is_set():raise BlockingIOError(errno.EAGAIN,'synthetic slow peer')
            requested=len(buf);result=super()._sock_send(buf[:127] if partial.is_set() else buf)
            send_sizes.append((requested,result));return result
        def publish(self,*args,**kwargs):
            result=super().publish(*args,**kwargs);published_depths.append(len(self._out_packet));return result
    monkeypatch.setattr(mqtt,'Client',SlowClient)
    io=DriveLinkIO('127.0.0.1',broker,None,None,(A,B))
    try:
        assert subscribed.wait(2);io.start()
        until(lambda:io.connected and owners and not owners[0].want_write())
        owner=owners[0];assert owner._sockpairR is None and owner._sockpairW is None
        blocked.set()
        def offer(sequence):
            value=reply();value.clock.probe.sequence=sequence;prepared=PreparedClock(value)
            io.offer(inbound(A),prepared,'clock');return prepared
        offer(1);until(owner.want_write)
        for sequence in range(2,201):latest=offer(sequence)
        # Repeated network attempts may not consume this replaceable slot.
        until(lambda:io.pending.get((inbound(A),'clock')) is latest)
        assert io.sent==1 and len(owner._out_packet)==1 and io.high_water==1
        assert owner._sockpairR is None and owner._sockpairW is None
        if release:
            partial.set();blocked.clear()
            until(lambda:len(received)==2)
            assert [v.clock.probe.sequence for v in received]==[1,200]
            assert received[-1].clock.vehicle==reply().clock.vehicle
            assert received[-1].clock.pi_send_us>0 and io.sent==2
            assert any(0<n<requested for requested,n in send_sizes)
            assert not io.pending and not owner.want_write()
        else:
            began=time.monotonic();io.close()
            assert time.monotonic()-began<1 and not io.thread.is_alive()
        assert max(published_depths)<=1 and io.failure is None
    finally:
        blocked.clear();io.close();reader.disconnect();reader.loop_stop()


def test_read_callback_closing_socket_does_not_write_to_the_old_descriptor(monkeypatch):
    io=DriveLinkIO('127.0.0.1',1,None,None,(A,B));sock=object();client=ExternalClient(sock,writing=True)
    def close_on_read():client.calls.append('read');client.sock=None;return mqtt.MQTT_ERR_SUCCESS
    client.loop_read=close_on_read
    monkeypatch.setattr(drive_link.select,'select',lambda *args:([sock],[sock],[]))
    assert io._network_once(client,mqtt)==mqtt.MQTT_ERR_SUCCESS
    assert client.calls==['read','misc']
