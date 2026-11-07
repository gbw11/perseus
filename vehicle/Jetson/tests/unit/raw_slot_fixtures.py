"""P0 codec용 합성 원본. 실제 capture/FILLED/메모리 발행 사실을 증명하지 않는다."""

import hashlib
import json
import struct
from uuid import UUID
import zlib

from raw_lease_fixtures import identifier, scope
from xenova_vehicle.runtime.raw_leases import FilledInfo, WriteKey
from xenova_vehicle.runtime.raw_slot import ExpectedSlot, P0Context


def context(*, nn_configured=True, slot=0, generation=1):
    names = ('rgb_video', 'depth_right', 'nn_input0', 'nn_output0')
    streams = {name: identifier(101 + i) for i, name in enumerate(names)}
    if not nn_configured:
        streams = {name: streams[name] for name in names[:2]}
    return P0Context(WriteKey(scope(), slot, generation, identifier(100)), streams, nn_configured)


def descriptor(*, nn_present=True):
    def stamp(number, sequence, device, sdk, received, exposure=None, bridge=None):
        return dict(source_key=[identifier(number), str(sequence), str(device)], sdk_us=str(sdk),
                    received_j_us=str(received), bridge_sample=bridge,
                    sdk_host_j_interval_us=['899', '903'] if bridge is not None else None,
                    exposure_us=exposure, capture_j_interval_us=None)

    bridge = dict(j_before_us='1000', sdk_us='2000', j_after_us='1002')
    streams = dict(rgb_video=stamp(101, 10, 2737300, 1900, 1502, 30006, bridge),
                   depth_right=stamp(102, 17, 2736575, 1850, 1510, 30005))
    planes = dict(rgb_video=dict(offset=4096, length=345600, dtype='u1', encoding='NV12',
                                shape=[540, 640], strides_bytes=[640, 1]),
                  depth_right=dict(offset=349696, length=614400, dtype='<u2', encoding='RAW16_LE',
                                   shape=[480, 640], strides_bytes=[1280, 2]))
    nn = None
    if nn_present:
        streams['nn_input0'] = stamp(103, 10, 2737300, 1900, 1490)
        streams['nn_output0'] = stamp(104, 10, 2737300, 1900, 1500)
        planes['nn_output0'] = dict(offset=964096, length=894848, dtype='u1',
                                   encoding='NN_STORAGE', shape=[894848], strides_bytes=[1])
        layers = [dict(name='det_out', data_type_code=0, order_code=801, dims=[1, 6300, 6],
                       strides_bytes=[75600, 12, 2], offset=0, length=75600)]
        for name, offset in (('drive_area_seg', 75648), ('lane_line_seg', 485248)):
            layers.append(dict(name=name, data_type_code=0, order_code=17185,
                               dims=[1, 2, 320, 320], strides_bytes=[409600, 204800, 640, 2],
                               offset=offset, length=409600))
        nn = dict(rgb_parent_key=streams['rgb_video']['source_key'],
                  input_key=streams['nn_input0']['source_key'],
                  output_key=streams['nn_output0']['source_key'], layers=layers)
    return dict(version=1, profile='p0-yolop-320-v1', raw_binding_sha256='a' * 64,
                streams=streams, planes=planes, nn=nn)


def payload(desc):
    end = max(plane['offset'] + plane['length'] for plane in desc['planes'].values())
    if not 4096 < end <= 2097152:
        raise ValueError('test fixture allocation is bounded to one slot')
    return bytes(end - 4096)


def bundle(*, nn_present=True, nn_configured=True):
    desc = descriptor(nn_present=nn_present)
    return context(nn_configured=nn_configured), desc, payload(desc)


def expected(encoding, *, completed_us=2000):
    return ExpectedSlot(encoding.context, FilledInfo(encoding.context.write_key,
                        encoding.payload_length, encoding.descriptor_sha256, completed_us))


def raw_record(ctx, desc, body=None, *, descriptor_raw=None, completed_us=2000):
    """제품 encoder와 독립적으로 header/CRC를 만든다. schema 음성 입력도 보존한다."""
    if body is None:
        body = payload(desc)
    raw = (json.dumps(desc, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode('ascii')
           if descriptor_raw is None else descriptor_raw)
    key = ctx.write_key
    header = struct.pack('<8sHHI16sQ16s32sIII28s', b'XNVRAW1\0', 1, 128, len(raw),
                         UUID(key.scope.arena_id).bytes, key.generation, UUID(key.write_token).bytes,
                         bytes.fromhex(key.scope.raw_binding_sha256), len(body),
                         zlib.crc32(raw, 0), zlib.crc32(body, 0), bytes(28))
    info = FilledInfo(key, len(body), hashlib.sha256(raw).hexdigest(), completed_us)
    return header + raw + bytes(max(0, 3968 - len(raw))) + body, ExpectedSlot(ctx, info)
