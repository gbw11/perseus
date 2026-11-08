"""S↔M의 유한 binary 통로. raw 영상·DB copy·이동 권한은 운반하지 않는다.

기존4KiB 제어 통로와 분리한다. header≤4096B/body≤32768B/packet≤40960B,
송신 pending 하나, 회차 수신 최대4개이며 역할별 typed consumer가 의미를 검사한다.
SOURCE_IDENTITY는 body 없는 제안이고 SOURCE_IDENTITY_ACK도 준비/주행 승인이 아니다.
"""

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass,field
import json
import socket
import struct
import threading
import time
from uuid import UUID,uuid4
import zlib

from .local_envelope import _bounded_tree,_pairs


HEADER_CAP=4096
BODY_CAP=32768
PACKET_CAP=40960
FIELDS=frozenset(('version','service_instance','channel_epoch','mapper_worker','sender',
    'message_sequence','kind','request_id','issued_j_us','expires_j_us','payload_length',
    'payload_crc32','metadata'))
OUTBOUND={'M':frozenset(('SOURCE_IDENTITY','WORLD_CANDIDATE','SURVEY_SUMMARY','SOURCE_BOOTSTRAP_LOAD','SOURCE_BOOTSTRAP_COMPLETE')),
          'S':frozenset(('SOURCE_IDENTITY_ACK','SOURCE_PUBLICATION','SOURCE_VEHICLE','SOURCE_TOF','SOURCE_CLOCK','SOURCE_SCENE','SOURCE_STORAGE','SOURCE_STORAGE_ABORT'))}


def _uuid(value):
    if type(value) is not str or str(UUID(value))!=value or not UUID(value).int:
        raise ValueError('canonical nonzero data-channel UUID required')
    return value


def _decimal(value,*,positive=False):
    if (type(value) is not str or not 1<=len(value)<=20 or not value.isascii()
            or not value.isdecimal() or str(int(value))!=value):
        raise ValueError('canonical data-channel uint64 string required')
    number=int(value)
    if not 0<=number<2**64 or positive and number==0:
        raise ValueError('data-channel uint64 outside range')
    return number


@dataclass(frozen=True)
class DataPeer:
    service_instance: str
    mapper_worker: str
    channel_epoch: str

    def __post_init__(self):
        for value in (self.service_instance,self.mapper_worker,self.channel_epoch):_uuid(value)


@dataclass(frozen=True)
class DataPacket:
    raw: bytes
    _header: dict=field(init=False,repr=False,compare=False)
    body: bytes=field(init=False)

    def __post_init__(self):
        if type(self.raw) is not bytes or not 8<=len(self.raw)<=PACKET_CAP:
            raise ValueError('bounded immutable binary packet required')
        header_length,body_length=struct.unpack_from('<II',self.raw)
        if not 0<header_length<=HEADER_CAP or body_length>BODY_CAP or len(self.raw)!=8+header_length+body_length:
            raise ValueError('binary length header exceeds original packet')
        header_bytes=self.raw[8:8+header_length]
        header=json.loads(header_bytes.decode('utf-8'),object_pairs_hook=_pairs,
                          parse_constant=lambda value:(_ for _ in ()).throw(ValueError('nonfinite header')))
        _bounded_tree(header,enforce_encoded_cost=False)
        if type(header) is not dict or set(header)!=FIELDS or type(header['version']) is not int or header['version']!=1:
            raise ValueError('unsupported binary header fields/version')
        for key in ('service_instance','channel_epoch','mapper_worker','request_id'):_uuid(header[key])
        if header['sender'] not in OUTBOUND or header['kind'] not in OUTBOUND[header['sender']]:
            raise ValueError('binary kind differs from sender role')
        _decimal(header['message_sequence'],positive=True)
        issued,expires=(_decimal(header[key]) for key in ('issued_j_us','expires_j_us'))
        if not issued<expires<=issued+5_000_000:
            raise ValueError('binary packet exceeds original five-second job bound')
        if (type(header['metadata']) is not dict or type(header['payload_length']) is not int
                or header['payload_length']!=body_length or type(header['payload_crc32']) is not int
                or not 0<=header['payload_crc32']<2**32):
            raise ValueError('binary body descriptor mismatch')
        body=self.raw[8+header_length:]
        if zlib.crc32(body)!=header['payload_crc32']:
            raise ValueError('binary body CRC mismatch')
        if header['kind']=='SOURCE_IDENTITY' and body:
            raise ValueError('identity proposal must have no body')
        if header['kind']=='SURVEY_SUMMARY' and len(body)>512:
            raise ValueError('original survey bytes exceed512 bytes')
        if header['kind']=='SOURCE_PUBLICATION' and set(header['metadata'])!={'source','origin','geometry_origin','context_sha256'}:
            raise ValueError('exact original publication metadata required')
        if header['kind']=='SOURCE_STORAGE_ABORT' and len(body)>8192:
            raise ValueError('original storage abort history exceeds8192 bytes')
        if header['kind']=='SOURCE_BOOTSTRAP_COMPLETE' and len(body)>8192:
            raise ValueError('original bootstrap completion exceeds8192 bytes')
        if header['kind'] not in ('WORLD_CANDIDATE','SOURCE_STORAGE_ABORT','SOURCE_BOOTSTRAP_COMPLETE') and len(body)>2048:
            raise ValueError('small source/identity body exceeds2048 bytes')
        object.__setattr__(self,'_header',header)
        object.__setattr__(self,'body',body)

    @property
    def header(self):return deepcopy(self._header)

    @property
    def kind(self):return self._header['kind']

    @property
    def issued_us(self):return int(self._header['issued_j_us'])

    @property
    def expires_us(self):return int(self._header['expires_j_us'])


@dataclass(frozen=True)
class DataSent:
    packet: DataPacket
    started_us: int
    completed_us: int


def data_pair():
    """실제 Linux seqpacket만 사용한다. 이름 있는 자원·새 semaphore는 생성하지 않는다."""
    if not hasattr(socket,'SOCK_SEQPACKET'):
        raise OSError('binary mapper data channel requires Linux seqpacket')
    endpoints=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
    try:
        for endpoint in endpoints:
            endpoint.setblocking(False)
            endpoint.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,2*PACKET_CAP)
            endpoint.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,2*PACKET_CAP)
    except BaseException:
        for endpoint in endpoints:
            try:endpoint.close()
            except OSError:pass
        raise
    return endpoints


class DataLink:
    """각 endpoint는 생성 thread 하나만 소유한다. 수신은 원 bytes/시각을 보존한다."""

    def __init__(self,endpoint,peer,role,*,clock=lambda:time.monotonic_ns()//1000):
        if (type(peer) is not DataPeer or role not in OUTBOUND or endpoint.family!=socket.AF_UNIX
                or endpoint.type!=getattr(socket,'SOCK_SEQPACKET',-1)):
            raise ValueError('registered Linux data endpoint required')
        endpoint.setblocking(False)
        self.endpoint,self.peer,self.role,self.clock=endpoint,peer,role,clock
        self.owner=threading.current_thread()
        self._pending=None
        self._next=1
        self._highwater=0
        self._last=None
        self.counts=Counter()
        self.closed=False

    def _check_owner(self):
        if threading.current_thread() is not self.owner:
            raise RuntimeError('binary endpoint belongs to a different thread')

    def send(self,kind,metadata,body=b'',*,request_id=None,issued_us=None,expires_us=None):
        self._check_owner()
        if self.closed:raise EOFError('binary data endpoint is closed')
        if self._pending is not None:return None
        if kind not in OUTBOUND[self.role] or type(body) is not bytes or len(body)>BODY_CAP:
            raise ValueError('unsupported outgoing binary kind/body')
        if self._next>=2**64:raise OverflowError('binary message sequence exhausted')
        issued=self.clock() if issued_us is None else issued_us
        expires=issued+5_000_000 if expires_us is None else expires_us
        header=dict(version=1,service_instance=self.peer.service_instance,channel_epoch=self.peer.channel_epoch,
            mapper_worker=self.peer.mapper_worker,sender=self.role,message_sequence=str(self._next),
            kind=kind,request_id=str(uuid4()) if request_id is None else request_id,
            issued_j_us=str(issued),expires_j_us=str(expires),payload_length=len(body),
            payload_crc32=zlib.crc32(body),metadata=metadata)
        _bounded_tree(header)
        encoded=json.dumps(header,sort_keys=True,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode('ascii')
        packet=DataPacket(struct.pack('<II',len(encoded),len(body))+encoded+body)
        self._pending=packet
        self._next+=1
        return packet

    def flush(self):
        """반환 DataSent만 실제 full-send 사실이다. queue 예약을 송신 완료로 세지 않는다."""
        self._check_owner()
        if self.closed:raise EOFError('binary data endpoint is closed')
        packet=self._pending
        if packet is None:return None
        if self.clock()>=packet.expires_us:
            raise TimeoutError('original pending binary packet expired before actual send')
        started=self.clock()
        try:sent=self.endpoint.send(packet.raw)
        except BlockingIOError:return None
        if sent!=len(packet.raw):raise OSError('partial seqpacket binary send')
        completed=self.clock()
        self._pending=None
        self.counts['sent']+=1
        return DataSent(packet,started,completed)

    def poll(self):
        self._check_owner()
        if self.closed:raise EOFError('binary data endpoint is closed')
        values=[]
        for _ in range(4):
            try:raw,ancillary,flags,address=self.endpoint.recvmsg(PACKET_CAP+1)
            except BlockingIOError:break
            if not raw:
                if values:break
                raise EOFError('binary data peer closed')
            if ancillary or flags&getattr(socket,'MSG_TRUNC',0):raise ValueError('truncated binary packet')
            packet=DataPacket(raw);header=packet.header
            if (header['service_instance'],header['mapper_worker'],header['channel_epoch'],header['sender'])!=(
                    self.peer.service_instance,self.peer.mapper_worker,self.peer.channel_epoch,'M' if self.role=='S' else 'S'):
                raise ValueError('binary packet differs from registered source/channel')
            sequence=int(header['message_sequence'])
            if sequence<=self._highwater:
                if sequence==self._highwater and raw==self._last:
                    self.counts['duplicate']+=1
                    continue
                raise ValueError('binary source sequence regressed or changed bytes')
            self._highwater,self._last=sequence,raw
            self.counts['received']+=1
            values.append(packet)
        return tuple(values)

    def close(self):
        self._check_owner()
        if not self.closed:
            self.endpoint.close();self.closed=True;self._pending=None
