"""raw 원장용 고정 합성 원본. 실제 arena/token/OS 사실을 생성하거나 승인하지 않는다."""

from xenova_vehicle.runtime.raw_leases import (
    Endpoint, FilledInfo, LeaseRequest, RawLeaseLedger, RawScope, ReadRequest,
)


def identifier(number):
    return '00000000-0000-4000-8000-{:012x}'.format(number)


def scope():
    return RawScope(identifier(1), identifier(6), Endpoint(identifier(2), identifier(3)),
                    'a' * 64, 'xenova-synthetic-raw')


def reader():
    return Endpoint(identifier(4), identifier(5))


def request(number=10, *, issued=100, expires=200, cap=None):
    return LeaseRequest(identifier(number), issued, expires, cap)


def ledger(*, publication=None):
    return RawLeaseLedger(scope(), reader(), publication_sha256=publication)


def write_ready(value, *, slot=0, request_number=10, token_number=20, issued=100,
                expires=200, granted=110, completed=120, received=130):
    grant = value.reserve_write(slot, request(request_number, issued=issued, expires=expires),
                                identifier(token_number), now_us=granted).value
    info = FilledInfo(grant.key, 960000, 'b' * 64, completed)
    value.filled(info, now_us=received)
    return grant, info


def read_lease(value, *, slot=0, request_number=30, token_number=40, issued=140,
               expires=300, reserved=150, publication=None):
    offer = value.offer(slot, now_us=reserved)
    original = ReadRequest(offer, reader(), request(request_number, issued=issued, expires=expires),
                           publication_sha256=publication)
    return value.acquire(original, identifier(token_number), now_us=reserved).value
