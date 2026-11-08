"""실제 UUID 발급/송신이 아닌 고정 합성 draft 입력을 만든다. wire sequence는 만들지 않는다."""

from lifecycle_message_fixtures import message_fields, peer
from xenova_vehicle.runtime.tx_outbox import LifecycleDraft


def request(number):
    return '00000000-0000-4000-8000-{:012x}'.format(number)


def draft(kind='HELLO', *, current=None, request_id=None, issued=100, expires=200,
          cap_us=None, revision=1, drain=180, payload=None):
    current = peer() if current is None else current
    request_id = request(4) if request_id is None else request_id
    fields = message_fields(kind, current=current, request_id=request_id, issued=issued,
                            expires=expires, revision=revision, drain=drain, payload=payload)
    return LifecycleDraft(current, current.role if kind == 'STOP' else 'S', kind,
                          request_id, issued, expires, fields['payload'], cap_us=cap_us)
