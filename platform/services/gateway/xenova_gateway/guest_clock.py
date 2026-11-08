"""원 I/O-stamped DriveLink/P4 receipt의 Pi owner. ACK·재전달은 원 lease를 연장하지 않는다."""
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256

from xenova_drive_world import decode,schema,PreparedClock,MAX_BYTES
from xenova_drive_world.mapper_clock import drift_pad


OWNER_PEERS=4
PER_PEER=32


@dataclass(frozen=True)
class GuestClockPolicy:
    timestamp_error_us: int
    drift_ppm: int
    max_roundtrip_us: int
    max_offset_width_us: int
    lease_us: int

    def __post_init__(self):
        fields=((self.timestamp_error_us,0,1000000),(self.drift_ppm,0,999999),
            (self.max_roundtrip_us,1,1000000),(self.max_offset_width_us,1,2**31-1),(self.lease_us,1,1000000))
        if any(type(v) is not int or not lo<=v<=hi for v,lo,hi in fields):raise ValueError('explicit Pi/guest clock bounds')


@dataclass(frozen=True,slots=True)
class SentClock:
    peer: tuple
    generation: int
    raw: bytes


@dataclass(frozen=True,slots=True)
class GuestLease:
    peer: tuple
    generation: int
    sequence: int
    lower: int
    upper: int
    expires: int
    support_guest_us: int
    digest: bytes
    p4: int


class OwnedPreparedClock(PreparedClock):
    """Gateway-local immutable source binding; common codec PreparedClock is unchanged."""
    def __new__(cls,message,peer,generation,replay=None):
        obj=super().__new__(cls,message)
        object.__setattr__(obj,'peer',peer);object.__setattr__(obj,'generation',generation)
        object.__setattr__(obj,'replay',replay)
        return obj

    def stamp(self,send_us):
        return self.replay if self.replay is not None else super().stamp(send_us)


class GuestClockOwner:
    def __init__(self,policy,host,instance):
        if policy is not None and type(policy) is not GuestClockPolicy:raise ValueError('guest clock policy')
        self.policy,self.host,self.instance=policy,host,instance
        self.sent=OrderedDict();self.sent_ends={};self.confirmed=OrderedDict();self.probes={};self.rejected=0;self.registered=None

    def register_peers(self,peers):
        """현재 등록 VM/role source만 유지한다. control generation은 clock source가 아니다."""
        values=frozenset(peers)
        if len(values)>OWNER_PEERS or any(type(p) is not tuple or len(p)!=3 for p in values):raise ValueError('four registered clock sources')
        self.registered=values
        for table in (self.sent,self.confirmed):
            for key in tuple(table):
                if key[:-1] not in values:del table[key]
        self.sent_ends={key:end for key,end in self.sent_ends.items() if key in self.sent}
        self.probes={p:v for p,v in self.probes.items() if p in values}

    def _admit_peer(self,peer):
        if self.registered is not None:
            if peer not in self.registered:raise ValueError('unregistered clock source')
        elif peer not in self.probes and peer not in {key[:-1] for key in self.sent} and len(
                set(self.probes)|{key[:-1] for key in self.sent})>=OWNER_PEERS:raise ValueError('clock peer capacity')

    @staticmethod
    def _end(completed):
        reply=decode(completed.raw,schema.DriveLink).clock
        return reply.guest_clock_valid_until_pi_us if reply.HasField('guest_clock_valid_until_pi_us') else reply.pi_receive_us+1_000_000

    def _prune(self,now):
        for key,end in tuple(self.sent_ends.items()):
            if now>=end:
                self.sent.pop(key,None);del self.sent_ends[key]
        for key,value in tuple(self.confirmed.items()):
            if now>=value.expires:del self.confirmed[key]

    def sent_reply(self,completed):
        if type(completed) is not SentClock:raise ValueError('original stamped reply required')
        raw=completed.raw
        if type(raw) is not bytes or not 0<len(raw)<=MAX_BYTES:raise ValueError('bounded original raw clock reply')
        reply=decode(raw,schema.DriveLink).clock
        self._admit_peer(completed.peer);self._prune(reply.pi_send_us)
        if reply.host_boot!=self.host or reply.gateway_instance!=self.instance:raise ValueError('clock send origin')
        peer=(reply.probe.vm,reply.probe.execution,reply.probe.app_instance)
        if peer!=completed.peer:raise ValueError('clock send binding')
        key=(*peer,reply.probe.sequence)
        old=self.sent.get(key)
        if old is not None:
            if old.raw!=raw:raise ValueError('same clock reply changed')
            return
        if sum(k[:-1]==peer for k in self.sent)>=PER_PEER:raise ValueError('clock source raw capacity')
        self.sent[key]=completed
        self.sent_ends[key]=reply.guest_clock_valid_until_pi_us if reply.HasField('guest_clock_valid_until_pi_us') else reply.pi_receive_us+1_000_000

    def accept_probe(self,probe,peer,generation,now):
        """원 duplicate는 원 Reply bytes만 재전달한다. 새 Probe는 원 P4 ACK만 돌려준다."""
        self._admit_peer(peer);self._prune(now)
        old=self.probes.get(peer)
        raw=probe.SerializeToString(deterministic=True)
        if old is not None and probe.sequence<=old[0]:
            if probe.sequence!=old[0] or raw!=old[1]:raise ValueError('probe sequence conflict')
            cached=self.sent.get((*peer,probe.sequence))
            if cached is None:raise ValueError('original duplicate reply retired or not sent')
            return None,cached.raw
        if not probe.sequence or not probe.guest_send_us:raise ValueError('probe origin')
        self.probes[peer]=(probe.sequence,raw)
        if self.policy is None or not probe.HasField('receipt'):return None,None
        receipt=probe.receipt;key=(*peer,receipt.probe_sequence);original=self.sent.get(key)
        if original is None:raise ValueError('original reply unavailable')
        reply=decode(original.raw,schema.DriveLink).clock;p=self.policy
        if (sha256(original.raw).digest()!=receipt.reply_sha256 or receipt.probe_sequence>=probe.sequence
                or not reply.probe.guest_send_us<=receipt.guest_receive_us<=probe.guest_send_us
                or not reply.HasField('guest_clock_valid_until_pi_us')
                or not reply.pi_send_us<reply.guest_clock_valid_until_pi_us<=reply.pi_receive_us+p.lease_us
                or now>=reply.guest_clock_valid_until_pi_us):raise ValueError('receipt parent/original lease')
        elapsed=receipt.guest_receive_us-reply.probe.guest_send_us
        if elapsed>p.max_roundtrip_us or reply.pi_send_us<reply.pi_receive_us:
            raise ValueError('receipt roundtrip/order')
        span=reply.guest_clock_valid_until_pi_us-reply.pi_receive_us
        expansion=2*p.timestamp_error_us+drift_pad(p.drift_ppm,elapsed+2*span)
        lower=reply.pi_send_us-receipt.guest_receive_us-expansion
        upper=reply.pi_receive_us-reply.probe.guest_send_us+expansion
        if not -(2**63-1)<=lower<=upper<=2**63-1 or upper-lower>p.max_offset_width_us:
            raise ValueError('receipt interval')
        lease=GuestLease(peer,original.generation,receipt.probe_sequence,lower,upper,reply.guest_clock_valid_until_pi_us,
            reply.probe.guest_send_us,receipt.reply_sha256,receipt.guest_receive_us)
        existing=self.confirmed.get(key)
        if existing is not None and existing!=lease:raise ValueError('confirmed clock changed')
        if existing is None and sum(k[:-1]==peer for k in self.confirmed)>=PER_PEER:
            raise ValueError('clock source confirmed capacity')
        self.confirmed[key]=lease
        return receipt,None

    def lease(self,peer,sequence,now,generation):
        value=self.confirmed.get((*peer,sequence))
        # Numeric clock proof is source-scoped. Actual control generation/owner
        # is independently checked by command adoption, including prepared B.
        return value if value is not None and now<value.expires else None

    def interval(self,peer,sequence,guest_us,now,generation):
        lease=self.lease(peer,sequence,now,generation)
        if lease is None or guest_us<lease.support_guest_us:return None
        lower,upper=guest_us+lease.lower,guest_us+lease.upper
        # Actual message arrival causally caps completion; it never extends E.
        upper=min(upper,now)
        return (lower,upper,lease.expires) if 0<=lower<=upper<=now<2**63 else None
