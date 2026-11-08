"""SLOT의 합성 원 시각/문맥. fixture 기본 예산은 실제 runtime 기본이나 수락값이 아니다."""

from dataclasses import replace

from raw_binding_fixtures import bind_message, context as bind_context
from raw_lease_fixtures import identifier, scope
from xenova_vehicle.runtime.lifecycle_messages import LifecyclePeer
from xenova_vehicle.runtime.local_envelope import encode_structure as packet
from xenova_vehicle.runtime.raw_binding_messages import RawBindContext
from xenova_vehicle.runtime.slot_messages import ReaderSlotContext, SlotMessage, SlotTimeLimits, WriterSlotContext


def limits(*, copy=1000, offer=500, read=150):
    return SlotTimeLimits(copy, offer, read)


def writer_context():
    bind = bind_message(bind_context('A'))
    return WriterSlotContext(bind, replace(scope(), raw_binding_sha256=bind.binding.sha256))


def reader_context(writer=None, *, mapper_number=5, channel_number=4):
    writer = writer_context() if writer is None else writer
    peer = LifecyclePeer(writer.scope.service_instance, identifier(channel_number), identifier(mapper_number), 'M', 'f' * 64)
    context = RawBindContext(peer, writer.scope.writer, writer.bind.context.profile)
    bind = bind_message(context, binding=writer.bind.binding, revision=writer.bind.binding_revision,
                        request_number=301, sequence=11, issued=200, expires=5000200)
    return ReaderSlotContext(writer, bind)


def slot_payload(op, context):
    writer = context if type(context) is WriterSlotContext else context.writer
    value = dict(op=op, arena_id=writer.scope.arena_id, slot=None, generation=None, write_token=None,
                 read_token=None, producer=writer.scope.writer.worker_instance, consumer=None,
                 raw_binding_sha256=writer.scope.raw_binding_sha256, publication_binding_sha256=None,
                 purpose=None, payload_length=None, descriptor_sha256=None)
    if op != 'REQUEST':
        value.update(slot=0, generation='1', write_token=identifier(400))
    if op in ('FILLED', 'OFFER', 'ACQUIRE', 'ACQUIRED'):
        value.update(payload_length=960000, descriptor_sha256='c' * 64)
    if op in ('OFFER', 'ACQUIRE', 'ACQUIRED'):
        value['consumer'] = context.reader.worker_instance
    if op in ('ACQUIRE', 'ACQUIRED'):
        value['purpose'] = 'PARSER_WARMUP'
    if op == 'ACQUIRED':
        value['read_token'] = identifier(401)
    return value


def slot_fields(op, context, *, request_number=None, issued=None, expires=None, sequence=1, payload=None):
    writer = context if type(context) is WriterSlotContext else context.writer
    peer = writer.bind.context.peer if type(context) is WriterSlotContext else context.bind.context.peer
    ids = dict(REQUEST=410, GRANT=410, FILLED=410, OFFER=411, ACQUIRE=412, ACQUIRED=412)
    starts = dict(REQUEST=10000000, GRANT=10000020, FILLED=10000040, OFFER=10000040,
                  ACQUIRE=10000050, ACQUIRED=10000060)
    ends = dict(REQUEST=10001000, GRANT=10001000, FILLED=10001000, OFFER=10000540,
                ACQUIRE=10000200, ACQUIRED=10000200)
    return dict(version=1, service_instance=writer.scope.service_instance, channel_epoch=peer.channel_epoch,
                sender_instance=peer.worker_instance if op in ('REQUEST', 'FILLED', 'ACQUIRE') else peer.service_instance,
                message_sequence=str(sequence), kind='SLOT', binding_domain='RAW',
                binding_revision=str(writer.bind.binding_revision),
                request_id=identifier(ids[op] if request_number is None else request_number),
                issued_j_us=str(starts[op] if issued is None else issued),
                expires_j_us=str(ends[op] if expires is None else expires),
                payload=slot_payload(op, context) if payload is None else payload)


def make(op, context, parent=None, *, limits=None, cap_us=None, fields=None, **wire):
    selected = SlotTimeLimits(1000, 500, 150) if limits is None else limits
    fields = slot_fields(op, context, **wire) if fields is None else fields
    return SlotMessage(packet(fields), context, parent, limits=selected, cap_us=cap_us)


def chain():
    writer = writer_context()
    reader = reader_context(writer)
    selected = limits()
    request = make('REQUEST', writer, limits=selected)
    grant = make('GRANT', writer, request, limits=selected)
    filled = make('FILLED', writer, grant, limits=selected)
    offer = make('OFFER', reader, limits=selected)
    acquire = make('ACQUIRE', reader, offer, limits=selected)
    acquired = make('ACQUIRED', reader, acquire, limits=selected)
    return dict(REQUEST=request, GRANT=grant, FILLED=filled, OFFER=offer, ACQUIRE=acquire, ACQUIRED=acquired)
