"""실제 seqpacket I/O 경계와 Queue1 actor fixture. ARM/권위/물리 STOP 시험은 아니다."""
import os
import queue
import socket
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from xenova_gateway import keyboard_contract as contract
from xenova_gateway.authority_journal import canonical
from xenova_gateway.keyboard_server import KeyboardServer

HOST = '11111111-1111-4111-8111-111111111111'
GATEWAY = '22222222-2222-4222-8222-222222222222'
SID = '33333333-3333-4333-8333-333333333333'
EPOCH = '44444444-4444-4444-8444-444444444444'


def call(kind='HELLO', *, source=None, channel=None, sequence=1):
    now = time.monotonic_ns()//1000
    if kind == 'HELLO':
        payload = dict(device_profile='control-keyboard', device_identity_sha256='a'*64, kernel_abi_sha256='b'*64)
    else:
        payload = dict(driver_session=SID, input_epoch=EPOCH, grant_revision='1', fence_revision='1',
                       key_event_seq='0', reason_mask=1, reason='OK')
    return dict(version=1, kind=kind, host_boot_id=HOST, gateway_instance_id=GATEWAY,
                source_instance=source or str(uuid4()), channel_epoch=channel or str(uuid4()),
                sequence=str(sequence), request_id=str(uuid4()), issued_pi_us=str(now),
                expires_pi_us=str(now+5000000), payload=payload)


class Actor:
    def __init__(self):
        self.calls = queue.Queue(maxsize=8)
        self.losses = queue.Queue(maxsize=8)
        self.outgoing = queue.Queue(maxsize=8)
        self.sequence = 0

    def submit(self, packet, peer):
        result = queue.Queue(maxsize=1)
        self.calls.put_nowait((packet, peer, result))
        return result

    def lost(self, peer):
        self.losses.put_nowait(peer)

    def take_outgoing(self):
        try:return self.outgoing.get_nowait()
        except queue.Empty:return None

    def answer(self, original, kind='STATE'):
        self.sequence += 1
        now=time.monotonic_ns()//1000
        if kind == 'STATE':
            payload=dict(driver_session=None,input_epoch=None,grant_revision='0',generation=1,state='DETACHED',
                reason='HELLO_ACCEPTED',reply_to_kind=original['kind'],reply_to_sequence=original['sequence'],
                accepted=True,accepted_state_seq=None)
        else:
            payload=dict(driver_session=SID,input_epoch=EPOCH,fence_revision='1',accepted_pi_us=str(now))
        return dict(version=1,kind=kind,host_boot_id=HOST,gateway_instance_id=GATEWAY,
            source_instance=original['source_instance'],channel_epoch=original['channel_epoch'],
            sequence=str(self.sequence),request_id=original['request_id'],issued_pi_us=str(now),
            expires_pi_us=str(now+5000000),payload=payload)


@pytest.fixture
def socket_directory():
    # Nano의 긴 개발/pytest 경로와 별개로 AF_UNIX의 실제 sun_path 한계를 지킨다.
    # 새 전용0700 디렉터리이며 제품의 canonical/소유권 검사는 그대로 실행한다.
    with tempfile.TemporaryDirectory(prefix='xk-',dir='/tmp') as directory:
        path=Path(directory)
        assert len(os.fsencode(path/'keyboard.sock'))<108
        yield path


@pytest.fixture
def server(socket_directory):
    actor=Actor();path=socket_directory/'keyboard.sock'
    service=KeyboardServer(actor,SimpleNamespace(fd=1),path,uid=os.geteuid()).start()
    try:yield service,actor,path
    finally:service.close()


def connect(path):
    stream=socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET)
    stream.settimeout(2)
    stream.connect(str(path))
    return stream


def test_real_peer_and_original_queue_result_are_forwarded_without_rewriting(server):
    service,actor,path=server
    with connect(path) as stream:
        original=call();raw=canonical(original);stream.send(raw)
        packet,peer,result=actor.calls.get(timeout=2)
        assert packet == original and peer.pid == os.getpid() and peer.uid == os.geteuid()
        answer=actor.answer(packet);result.put_nowait(answer)
        assert stream.recv(2048) == canonical(answer)
    assert actor.losses.get(timeout=2) == peer
    assert service.failure is None


def test_fence_lane_survives_pending_ordinary_and_duplicate_does_not_resubmit(server):
    _,actor,path=server
    with connect(path) as stream:
        normal=call();raw=canonical(normal);stream.send(raw)
        packet,peer,pending=actor.calls.get(timeout=2)
        stream.send(raw)
        fence=call('FENCE',source=normal['source_instance'],channel=normal['channel_epoch'],sequence=2)
        stream.send(canonical(fence))
        safety,same_peer,result=actor.calls.get(timeout=2)
        assert safety == fence and same_peer == peer
        answer=actor.answer(safety,'FENCE_ACK');result.put_nowait(answer)
        assert contract.decode(stream.recv(2048)) == answer
        answer=actor.answer(packet);pending.put_nowait(answer)
        assert contract.decode(stream.recv(2048)) == answer
        assert actor.calls.empty()


def test_truncated_packet_is_not_submitted_and_only_two_connections_are_held(server):
    service,actor,path=server
    first,second=connect(path),connect(path)
    try:
        # A tiny valid request proves each accepted slot before the third is tried.
        first.send(canonical(call()));_,one,_=actor.calls.get(timeout=2)
        second.send(canonical(call()));_,two,_=actor.calls.get(timeout=2)
        assert one != two and one.connection_id != two.connection_id
        with connect(path) as third:assert third.recv(2048) == b''
        first.send(b'x'*2049)
        assert first.recv(2048) == b''
        assert actor.losses.get(timeout=2) == one
        assert service.stats['busy'] >= 1 and service.stats['malformed'] >= 1
        assert actor.calls.empty()
    finally:first.close();second.close()


def test_closed_connection_identity_cannot_route_late_output_to_reused_peer(server):
    _,actor,path=server
    with connect(path) as stream:
        stream.send(canonical(call()));old,old_peer,_=actor.calls.get(timeout=2)
    assert actor.losses.get(timeout=2) == old_peer
    with connect(path) as stream:
        stream.send(canonical(call()));new,new_peer,result=actor.calls.get(timeout=2)
        assert new_peer != old_peer
        actor.outgoing.put_nowait((old_peer,actor.answer(old)))
        answer=actor.answer(new);result.put_nowait(answer)
        assert contract.decode(stream.recv(2048)) == answer


def test_close_preserves_replaced_endpoint_and_reports_own_thread_quiescence(socket_directory):
    path=socket_directory/'keyboard.sock';actor=Actor()
    service=KeyboardServer(actor,SimpleNamespace(fd=1),path,uid=os.geteuid()).start()
    path.unlink();path.write_bytes(b'new-owner')
    service.close()
    assert path.read_bytes() == b'new-owner'
    assert not service.thread.is_alive() and service.failure is None
