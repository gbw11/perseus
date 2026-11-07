"""RAW BIND용 합성 참조/등록 원본. parser/graph/보정 파일의 실존·승인을 생성하지 않는다."""

from raw_lease_fixtures import identifier, scope
from xenova_vehicle.runtime.lifecycle_messages import LifecyclePeer
from xenova_vehicle.runtime.local_envelope import encode_structure
from xenova_vehicle.runtime.raw_binding import RawBinding, RawBindingProfile
from xenova_vehicle.runtime.raw_binding_messages import RawBindContext, RawBindMessage


MODEL_SHA = '8bc407f90814c6092ce0d76f7ce8cee0aaec68236358113946fe0134a655c437'
DEVICE = '1844301051456A1200'


def binding_fields(*, nn_enabled=True, jpeg_enabled=False):
    streams = dict(rgb_video=identifier(101), depth_right=identifier(102))
    if nn_enabled:
        streams.update(nn_input0=identifier(103), nn_output0=identifier(104))
    if jpeg_enabled:
        streams['jpeg'] = identifier(105)
    return dict(service_instance=identifier(1), acquisition_worker=identifier(3),
                pipeline_instance=identifier(201), capture_epoch=identifier(202),
                stream_instances=streams, sdk_bridge_epoch=identifier(203), device_id=DEVICE,
                model_id=MODEL_SHA if nn_enabled else None, parser_id='b' * 64 if nn_enabled else None,
                graph_profile_sha256='c' * 64, calibration_bundle=None,
                format_table_sha256='d' * 64, clock_epoch=identifier(204))


def profile(*, nn_enabled=True, jpeg_enabled=False, calibration=None, device_id=DEVICE):
    return RawBindingProfile(device_id, identifier(204), MODEL_SHA if nn_enabled else None,
                             'b' * 64 if nn_enabled else None, 'c' * 64, 'd' * 64,
                             calibration, nn_enabled, jpeg_enabled)


def context(role='M', *, nn_enabled=True, jpeg_enabled=False):
    acquisition = scope().writer
    peer = LifecyclePeer(identifier(1), acquisition.channel_epoch if role == 'A' else identifier(4),
                         acquisition.worker_instance if role == 'A' else identifier(5), role, 'f' * 64)
    return RawBindContext(peer, acquisition, profile(nn_enabled=nn_enabled, jpeg_enabled=jpeg_enabled))


def bind_fields(ctx=None, binding=None, *, revision=1, request_number=300, issued=100,
                expires=5000100, sequence=10):
    ctx = context() if ctx is None else ctx
    if binding is None:
        binding = RawBinding(binding_fields(nn_enabled=ctx.profile.nn_enabled,
                                            jpeg_enabled=ctx.profile.jpeg_enabled))
    elif type(binding) is dict:
        binding = RawBinding(binding)
    return dict(version=1, service_instance=ctx.peer.service_instance,
                channel_epoch=ctx.peer.channel_epoch, sender_instance=ctx.peer.service_instance,
                message_sequence=str(sequence), kind='BIND', binding_domain='RAW',
                binding_revision=str(revision), request_id=identifier(request_number),
                issued_j_us=str(issued), expires_j_us=str(expires),
                payload=dict(binding_revision=str(revision), binding_sha256=binding.sha256,
                             binding=binding.fields))


def bind_packet(ctx=None, fields=None, **kwargs):
    return encode_structure(bind_fields(ctx, **kwargs) if fields is None else fields)


def bind_message(ctx=None, **kwargs):
    ctx = context() if ctx is None else ctx
    return RawBindMessage(bind_packet(ctx, **kwargs), ctx)


def ack_fields(original, *, accepted=True, state=None, reason=None, issued=200,
               expires=None, sequence=1):
    peer = original.context.peer
    if state is None:
        state = 'WARMUP' if peer.role == 'A' else 'WAIT_CLOCK'
    if not accepted and reason is None:
        reason = 'UNSUPPORTED_PROFILE'
    return dict(version=1, service_instance=peer.service_instance, channel_epoch=peer.channel_epoch,
                sender_instance=peer.worker_instance, message_sequence=str(sequence), kind='BIND_ACK',
                binding_domain='RAW', binding_revision=str(original.binding_revision),
                request_id=original.request_id, issued_j_us=str(issued),
                expires_j_us=str(original.expires_us if expires is None else expires),
                payload=dict(binding_revision=str(original.binding_revision),
                             binding_sha256=original.binding.sha256, accepted=accepted,
                             state=state, reason=reason))


def ack_packet(original, **kwargs):
    return encode_structure(ack_fields(original, **kwargs))
