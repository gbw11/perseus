"""임무 전송용 유한 사본 보관. 예약·내구 기록·phase/출력 권위는 소유하지 않는다.

단일 Gateway actor가 호출한다. 수신 문맥은 인증된 전송의 출처 표식일 뿐 현재 owner의
승인이 아니다. S18의 원 nonce 조회가 현재 generation·기한 검사보다 먼저 이뤄져야 하므로
이 모듈은 그 검사를 대신하지 않는다. encode나 grant 보관 성공도 승인 근거가 아니다.
"""

from collections import deque
from dataclasses import dataclass
from hashlib import sha256
from itertools import islice

from xenova_drive_world import decode,encode,schema


_MAX_U63=(1<<63)-1
_REQUEST_CAP=512
_REPLY_CAP=1536
_GRANT_CAP=1024
_BUSY=4
_REJECTED=5
_CONTEXT=1
_NONCE_CONFLICT=8


def _label(value):
    return (type(value) is str and 1<=len(value)<=36
            and all(33<=ord(char)<=126 for char in value))


def _canonical(message,message_type,cap):
    if type(message) is not message_type or message.ByteSize()>cap:
        raise ValueError('bounded shared mission message required')
    copy=message_type()
    copy.CopyFrom(message)
    copy.DiscardUnknownFields()
    return copy,encode(copy)


@dataclass(frozen=True,slots=True)
class BudgetEnvelope:
    """처음 수락한 known-field 요청과 원 수신 문맥. bytes만 원 본문을 소유한다."""

    vm: str
    execution: str
    app_instance: str
    authenticated_generation: int
    request_wire: bytes
    request_nonce: bytes
    request_sha256: bytes
    received_pi_us: int
    valid_until_pi_us: int

    def request_message(self):
        """매번 별도 mutable protobuf를 돌려준다. 원 사본이나 기한은 바뀌지 않는다."""
        return decode(self.request_wire,schema.MissionBudgetRequest)


class MissionLinkPort:
    """설치한 VM 두 개, VM당 미완료 요청 하나와 grant 한 개를 보관한다.

    budget 입력은 기본 거절이다. 실제 예약 소유자가 연결될 때만 enable한다. take 뒤에도
    complete까지 원 slot을 유지하며 재전송은 원 수신시각·기한·출처를 갱신하지 않는다.
    현재 권위/scene/STOP/기한 검증, 원 예약 조회·내구 commit과 grant 공개는 호출자 책임이다.
    """

    def __init__(self,vm_ids):
        values=tuple(islice(iter(vm_ids),3))
        if len(values)!=2 or not all(_label(vm) for vm in values) or len(set(values))!=2:
            raise ValueError('exactly two installed VM labels required')
        self.vm_ids=values
        self._enabled=False
        self._pending={}
        self._keys={}
        self._queued=deque()
        self._grants={}

    def enable_budget_input(self):
        """실제 소유자 연결을 위한 명시 opt-in. 권위를 생성하거나 요청을 처리하지 않는다."""
        self._enabled=True

    @staticmethod
    def _reply(envelope,result,reason):
        return schema.MissionBudgetReply(major=1,request_nonce=envelope.request_nonce,
            request_sha256=envelope.request_sha256,result=result,reason=reason)

    def offer_budget(self,vm,execution,app_instance,authenticated_generation,request,received_pi_us):
        """원 envelope 또는 즉시 typed BUSY/REJECTED를 반환한다.

        미지원은 REJECTED/CONTEXT, slot 포화는 BUSY/CONTEXT, 원 nonce의 본문 충돌은
        REJECTED/NONCE_CONFLICT다. 형식 오류는 ValueError이며 어떤 slot도 바꾸지 않는다.
        원 valid_until은 그대로 둔다. 만료/현재 generation 비교는 원 예약 조회 뒤 소유자가 한다.
        완료 후의 재요청은 S18 원장 조회를 위한 새 전송 사본이며 여기서 성공을 replay하지 않는다.
        """
        body,wire=_canonical(request,schema.MissionBudgetRequest,_REQUEST_CAP)
        if (not _label(vm) or not _label(execution) or not _label(app_instance)
                or type(authenticated_generation) is not int or not 0<=authenticated_generation<=0xffffffff
                or type(received_pi_us) is not int or not 0<=received_pi_us<=_MAX_U63):
            raise ValueError('bounded authenticated transport context required')
        envelope=BudgetEnvelope(vm,execution,app_instance,authenticated_generation,wire,
            bytes(body.request_nonce),sha256(wire).digest(),received_pi_us,body.valid_until_pi_us)
        if vm not in self.vm_ids or not self._enabled:return self._reply(envelope,_REJECTED,_CONTEXT)
        # nonce는 A8의 원 임무 범위에 속한다. owner 교체가 원 요청을 새 요청으로 만들지 않는다.
        key=(bytes(body.mission_id),body.mission_revision,envelope.request_nonce)
        for installed,original in self._pending.items():
            if self._keys[installed]==key:
                if original.request_wire!=wire:return self._reply(envelope,_REJECTED,_NONCE_CONFLICT)
                return original
        if vm in self._pending:return self._reply(envelope,_BUSY,_CONTEXT)
        self._pending[vm]=envelope
        self._keys[vm]=key
        self._queued.append(vm)
        return envelope

    def take_budget(self):
        """최대 두 원 요청을 수락 순서로 한 번씩 전달한다. 완료 전에는 slot을 재사용하지 않는다."""
        if not self._queued:return None
        return self._pending[self._queued.popleft()]

    def complete_budget(self,envelope,reply):
        """정확한 원 nonce/SHA의 bare MissionBudgetReply bytes를 반환하고 slot을 해제한다.

        reply의 원 grant/time/revision을 만들거나 cache에 공개하지 않는다. stale binding과
        실제 전송 대상을 확인하고 DriveLink major2로 감싸는 일은 호출자 책임이다.
        """
        if type(envelope) is not BudgetEnvelope or self._pending.get(envelope.vm) is not envelope:
            raise ValueError('original pending budget envelope required')
        body,wire=_canonical(reply,schema.MissionBudgetReply,_REPLY_CAP)
        if body.request_nonce!=envelope.request_nonce or body.request_sha256!=envelope.request_sha256:
            raise ValueError('budget completion nonce/fingerprint mismatch')
        del self._pending[envelope.vm]
        del self._keys[envelope.vm]
        if envelope.vm in self._queued:self._queued.remove(envelope.vm)
        return wire

    def publish_grant(self,vm,grant):
        """소유자가 사전 검증한 grant의 원 bare bytes를 VM별로 교체한다. 권위 검사는 하지 않는다."""
        if vm not in self.vm_ids:raise ValueError('installed VM required')
        _,wire=_canonical(grant,schema.MissionGrant,_GRANT_CAP)
        self._grants[vm]=wire
        return wire

    def current_grant(self,vm):
        """원 bare MissionGrant bytes 또는 None. 만료·current owner 검증이나 기한 갱신은 하지 않는다."""
        if vm not in self.vm_ids:raise ValueError('installed VM required')
        return self._grants.get(vm)
