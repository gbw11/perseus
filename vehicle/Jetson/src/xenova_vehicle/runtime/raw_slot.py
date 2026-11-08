"""소유한 P0 raw slot의 형식·출처 metadata·CRC를 검사하는 순수 codec.

128B prefix는 복사 길이만 제한한다. decoder는 원 FILLED까지 대조하지만 실제
lease/token/메모리 발행·RawBinding 전체 승인·센서 시각·FP16 finite·metric 자격을
증명하지 않는다. bytes 입력은 이미 소유한 사본이어야 하며 SDK/SHM view는 받지 않는다.
"""

from dataclasses import dataclass
import hashlib
import json
import re
import struct
from types import MappingProxyType
from uuid import UUID
import zlib

from .raw_leases import FilledInfo, WriteKey


HEADER_BYTES = 128
METADATA_BYTES = 4096
SLOT_BYTES = 2 * 1024 * 1024
MAX_DESCRIPTOR_BYTES = METADATA_BYTES - HEADER_BYTES
MAX_PAYLOAD_BYTES = SLOT_BYTES - METADATA_BYTES
MAX_NN_BYTES = 1048576
U64_MAX = 2**64 - 1
MAGIC = b'XNVRAW1\0'
PROFILE = 'p0-yolop-320-v1'
HEADER_STRUCT = struct.Struct('<8sHHI16sQ16s32sIII28s')
_STREAMS = ('rgb_video', 'depth_right', 'nn_input0', 'nn_output0')
_LAYERS = {
    'det_out': ([1, 6300, 6], [75600, 12, 2], 801, 75600),
    'drive_area_seg': ([1, 2, 320, 320], [409600, 204800, 640, 2], 17185, 409600),
    'lane_line_seg': ([1, 2, 320, 320], [409600, 204800, 640, 2], 17185, 409600),
}


class SlotCodecError(ValueError):
    """상한·형식·원 기대값 또는 내부 관계가 맞지 않는 owned slot."""


def _fail(message):
    raise SlotCodecError(message)


def _exact(value, expected):
    if type(value) is not expected:
        _fail('expected exact ' + expected.__name__)


def _integer(value, minimum=0, maximum=U64_MAX):
    if type(value) is not int or not minimum <= value <= maximum:
        _fail('integer outside its exact type/range')
    return value


def _uuid(value):
    if type(value) is not str or len(value) != 36:
        _fail('expected canonical UUID')
    try:
        normalized = str(UUID(value))
    except ValueError as error:
        raise SlotCodecError('invalid UUID') from error
    if normalized != value:
        _fail('expected canonical lowercase UUID')


def _decimal(value):
    if (type(value) is not str or not 1 <= len(value) <= 20
            or re.fullmatch('0|[1-9][0-9]*', value) is None):
        _fail('expected canonical u64 decimal string')
    return _integer(int(value))


def _keys(value, names):
    if type(value) is not dict or set(value) != set(names):
        _fail('missing, duplicate or unknown profile field')


def _same(value, expected):
    if type(value) is not type(expected) or value != expected:
        _fail('value differs from the fixed P0 profile')


def _copy_native(value):
    """각 출현을 세며 exact native tree를 한 번 복사한다. 경로 밖 alias는 허용한다."""
    count = 0
    active = set()

    def visit(item, container_depth):
        nonlocal count
        count += 1
        if count > 512:
            _fail('descriptor exceeds 512 key/value/container nodes')
        kind = type(item)
        if kind is str:
            if len(item) > 64 or any(ord(c) > 127 for c in item):
                _fail('descriptor string/key exceeds ASCII/64 bound')
            return item
        if kind is int:
            if item.bit_length() > 64:
                _fail('descriptor integer exceeds 64 bits')
            return item
        if item is None:
            return None
        if kind is not dict and kind is not list:
            _fail('descriptor accepts exact dict/list/str/int/None only')
        depth = container_depth + 1
        if depth > 5 or len(item) > (8 if kind is dict else 4):
            _fail('descriptor exceeds container depth/width bound')
        identity = id(item)
        if identity in active:
            _fail('descriptor contains an active-path cycle')
        active.add(identity)
        try:
            if kind is list:
                return [visit(child, depth) for child in item]
            result = {}
            for key, child in item.items():
                if type(key) is not str:
                    _fail('descriptor keys must be exact strings')
                own_key = visit(key, depth)
                result[own_key] = visit(child, depth)
            return result
        finally:
            active.remove(identity)

    return visit(value, 0)


def _lexical_depth(raw):
    """json.loads 전에 container nesting만 제한한다. 문법 판정은 JSON parser가 맡는다."""
    depth = 0
    quoted = escaped = False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > 5:
                _fail('raw descriptor container depth exceeds five')
        elif byte in (93, 125):
            depth -= 1
            if depth < 0:
                _fail('unbalanced raw descriptor')


def _parse_descriptor(raw):
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_DESCRIPTOR_BYTES:
        _fail('descriptor byte length/type exceeds metadata extent')
    if not raw.isascii():
        _fail('descriptor wire bytes must be ASCII')
    _lexical_depth(raw)

    def pairs(values):
        if len(values) > 8:
            _fail('raw object exceeds eight fields')
        result = {}
        for key, value in values:
            if key in result:
                _fail('duplicate decoded JSON key')
            result[key] = value
        return result

    def integer(text):
        digits = text[1:] if text.startswith('-') else text
        if len(digits) > 20:
            _fail('raw JSON integer exceeds 64 bits')
        value = int(text)
        if value.bit_length() > 64:
            _fail('raw JSON integer exceeds 64 bits')
        return value

    def reject_number(_):
        _fail('float/nonfinite JSON number is unsupported')

    try:
        value = json.loads(raw.decode('ascii'), object_pairs_hook=pairs, parse_int=integer,
                           parse_float=reject_number, parse_constant=reject_number)
    except (ValueError, RecursionError) as error:
        if isinstance(error, SlotCodecError):
            raise
        raise SlotCodecError('invalid descriptor JSON') from error
    # parser의 raw3968/depth5 한계와 소유 tree의 출현 수 한계는 별개다.
    return _copy_native(value)


@dataclass(frozen=True, init=False)
class P0Context:
    """이미 승인된 RawBinding의 필요한 표만 복사한다. 전체 binding의 해시는 계산하지 않는다."""

    write_key: WriteKey
    stream_ids: MappingProxyType
    nn_configured: bool

    def __init__(self, write_key, stream_ids, nn_configured):
        _exact(write_key, WriteKey)
        _exact(nn_configured, bool)
        _exact(stream_ids, dict)
        names = _STREAMS if nn_configured else _STREAMS[:2]
        if len(stream_ids) != len(names):
            _fail('registered P0 named stream subset differs')
        copied = {}
        for name, value in stream_ids.items():
            if type(name) is not str or name not in names:
                _fail('unexpected registered stream name')
            _uuid(value)
            copied[name] = value
        if set(copied) != set(names) or len(set(copied.values())) != len(copied):
            _fail('registered stream UUIDs must be distinct and complete')
        object.__setattr__(self, 'write_key', write_key)
        object.__setattr__(self, 'stream_ids', MappingProxyType(copied))
        object.__setattr__(self, 'nn_configured', nn_configured)


@dataclass(frozen=True)
class ExpectedSlot:
    """수신 descriptor가 아닌 원 FILLED와 caller 등록 문맥을 대조 기준으로 보유한다."""

    context: P0Context
    original_filled_info: FilledInfo

    def __post_init__(self):
        _exact(self.context, P0Context)
        _exact(self.original_filled_info, FilledInfo)
        if self.original_filled_info.key != self.context.write_key:
            _fail('original FILLED full write key differs from registered context')


@dataclass(frozen=True, init=False)
class PrefixHeader:
    """128B만 검사한다. descriptor/body/원 FILLED 검증이나 읽기 권한이 아니다."""

    raw: bytes
    context: P0Context
    descriptor_length: int
    payload_length: int
    descriptor_crc32: int
    payload_crc32: int

    def __init__(self, raw, context):
        _exact(context, P0Context)
        if type(raw) is not bytes or len(raw) != HEADER_BYTES:
            _fail('prefix requires exactly 128 owned bytes')
        magic, version, size, d, arena, generation, token, binding, length, dc, pc, reserved = HEADER_STRUCT.unpack(raw)
        key = context.write_key
        if magic != MAGIC or version != 1 or size != HEADER_BYTES or reserved != bytes(28):
            _fail('unsupported header magic/version/size/reserved')
        _integer(d, 1, MAX_DESCRIPTOR_BYTES)
        _integer(length, 1, MAX_PAYLOAD_BYTES)
        _integer(generation, 1)
        if (arena != UUID(key.scope.arena_id).bytes or generation != key.generation
                or token != UUID(key.write_token).bytes
                or binding != bytes.fromhex(key.scope.raw_binding_sha256)):
            _fail('header differs from the expected writer binding/key')
        for name, value in (('raw', raw), ('context', context), ('descriptor_length', d),
                            ('payload_length', length), ('descriptor_crc32', dc), ('payload_crc32', pc)):
            object.__setattr__(self, name, value)

    @property
    def used_length(self):
        return METADATA_BYTES + self.payload_length


def _source_key(value, stream_id):
    if type(value) is not list or len(value) != 3:
        _fail('source key must contain stream UUID, seq and device us')
    _uuid(value[0])
    if value[0] != stream_id:
        _fail('source key differs from the registered named stream UUID')
    return _decimal(value[1]), _decimal(value[2])


def _stamp(value, stream_id, *, nn, completed):
    _keys(value, ('source_key', 'sdk_us', 'received_j_us', 'bridge_sample',
                  'sdk_host_j_interval_us', 'exposure_us', 'capture_j_interval_us'))
    sequence, device = _source_key(value['source_key'], stream_id)
    sdk = _decimal(value['sdk_us'])
    received = _decimal(value['received_j_us'])
    if completed is not None and received > completed:
        _fail('original received J exceeds original FILLED completion')
    exposure = value['exposure_us']
    if exposure is not None:
        if nn:
            _fail('NN exposure is unknown in the initial profile')
        _integer(exposure, 0, 1000000)
    if value['capture_j_interval_us'] is not None:
        _fail('initial P0 capture interval is unknown/null-only')
    bridge = value['bridge_sample']
    interval = value['sdk_host_j_interval_us']
    if bridge is None:
        if interval is not None:
            _fail('SDK/J interval has no original bridge')
    else:
        _keys(bridge, ('j_before_us', 'sdk_us', 'j_after_us'))
        before, anchor, after = (_decimal(bridge[k]) for k in ('j_before_us', 'sdk_us', 'j_after_us'))
        if before > after or not 0 <= received - after <= 1000000:
            _fail('reversed/expired SDK/J bridge')
        if completed is not None and after > completed:
            _fail('original bridge.after exceeds original FILLED completion')
        lower, upper = sdk + before - anchor - 1, sdk + after - anchor + 1
        _integer(lower)
        _integer(upper)
        if lower > upper or type(interval) is not list or len(interval) != 2:
            _fail('invalid SDK/J interval')
        if [_decimal(endpoint) for endpoint in interval] != [lower, upper]:
            _fail('SDK/J interval differs from the original bridge including quantization')
    return sequence, device, sdk


def _plane(value, name, used_end):
    _keys(value, ('offset', 'length', 'dtype', 'encoding', 'shape', 'strides_bytes'))
    offset = _integer(value['offset'], METADATA_BYTES, SLOT_BYTES)
    length = _integer(value['length'], 1, MAX_PAYLOAD_BYTES)
    if offset % 64 or offset + length > used_end:
        _fail('plane alignment/span exceeds the declared used slot')
    if name == 'rgb_video':
        expected = (345600, 'u1', 'NV12', [540, 640], [640, 1])
    elif name == 'depth_right':
        expected = (614400, '<u2', 'RAW16_LE', [480, 640], [1280, 2])
    else:
        if length > MAX_NN_BYTES:
            _fail('NN storage exceeds actual-buffer cap')
        expected = (length, 'u1', 'NN_STORAGE', [length], [1])
    for field, wanted in zip(('length', 'dtype', 'encoding', 'shape', 'strides_bytes'), expected):
        _same(value[field], wanted)
    return offset, offset + length


def _disjoint(regions):
    ordered = sorted(regions)
    if any(a[1] > b[0] for a, b in zip(ordered, ordered[1:])):
        _fail('overlapping storage regions')


def _nn(value, streams, storage_length):
    _keys(value, ('rgb_parent_key', 'input_key', 'output_key', 'layers'))
    for name, stream in (('rgb_parent_key', 'rgb_video'), ('input_key', 'nn_input0'), ('output_key', 'nn_output0')):
        _same(value[name], streams[stream]['source_key'])
    layers = value['layers']
    if type(layers) is not list or len(layers) != 3:
        _fail('initial P0 requires three actual multidimensional YOLOP layers')
    names, regions = set(), []
    for layer in layers:
        _keys(layer, ('name', 'data_type_code', 'order_code', 'dims', 'strides_bytes', 'offset', 'length'))
        name = layer['name']
        if type(name) is not str or name not in _LAYERS or name in names:
            _fail('missing/duplicate/unknown YOLOP layer')
        names.add(name)
        dims, strides, order, length = _LAYERS[name]
        for field, wanted in (('data_type_code', 0), ('order_code', order), ('dims', dims),
                              ('strides_bytes', strides), ('length', length)):
            _same(layer[field], wanted)
        offset = _integer(layer['offset'], 0, MAX_NN_BYTES)
        if offset % 2 or offset + length > storage_length:
            _fail('tensor must be even-aligned inside the actual NN storage')
        regions.append((offset, offset + length))
    _disjoint(regions)


def _schema(value, context, payload_length, *, completed=None):
    _keys(value, ('version', 'profile', 'raw_binding_sha256', 'streams', 'planes', 'nn'))
    _same(value['version'], 1)
    _same(value['profile'], PROFILE)
    _same(value['raw_binding_sha256'], context.write_key.scope.raw_binding_sha256)
    present = value['nn'] is not None
    if present and not context.nn_configured:
        _fail('NN present in an NN-absent graph')
    names = _STREAMS if present else _STREAMS[:2]
    _keys(value['streams'], names)
    _keys(value['planes'], (*_STREAMS[:2], 'nn_output0') if present else _STREAMS[:2])
    stamps = {name: _stamp(value['streams'][name], context.stream_ids[name],
                          nn=name.startswith('nn_'), completed=completed) for name in names}
    if present and not stamps['rgb_video'] == stamps['nn_input0'] == stamps['nn_output0']:
        _fail('RGB/input/output seq/device/SDK association differs')
    used_end = METADATA_BYTES + payload_length
    regions = [_plane(plane, name, used_end) for name, plane in value['planes'].items()]
    _disjoint(regions)
    if max(end for _, end in regions) != used_end:
        _fail('payload extent must end at the latest plane end')
    if present:
        _nn(value['nn'], value['streams'], value['planes']['nn_output0']['length'])


class _DescriptorAccess:
    """파싱한 mutable tree를 보관하거나 빌려주지 않는다."""

    def descriptor(self):
        """작은 descriptor의 새 dict/list 사본. raw CRC/SHA에 쓰는 원 bytes는 그대로다."""
        return json.loads(self.descriptor_bytes)

    def payload_bytes(self):
        """사용 body 전체의 소유 bytes 사본. gap/tail도 포함하며 zero-copy가 아니다."""
        return self.raw[METADATA_BYTES:]


@dataclass(frozen=True, init=False)
class SlotEncoding(_DescriptorAccess):
    """producer 형식 결과. 아직 없는 FILLED 완료나 실제 copy/post를 발급하지 않는다."""

    context: P0Context
    raw: bytes
    header: PrefixHeader
    descriptor_bytes: bytes
    descriptor_sha256: str
    payload_length: int

    def __init__(self, context, native_descriptor, payload):
        _exact(context, P0Context)
        if type(payload) is not bytes or not 1 <= len(payload) <= MAX_PAYLOAD_BYTES:
            _fail('encoder needs exact owned body bytes within one slot')
        own = _copy_native(native_descriptor)
        _schema(own, context, len(payload))
        descriptor = json.dumps(own, sort_keys=True, ensure_ascii=True, separators=(',', ':'),
                                allow_nan=False).encode('ascii')
        if not 1 <= len(descriptor) <= MAX_DESCRIPTOR_BYTES:
            _fail('encoded descriptor exceeds metadata cap')
        key = context.write_key
        raw_header = HEADER_STRUCT.pack(MAGIC, 1, HEADER_BYTES, len(descriptor),
                                       UUID(key.scope.arena_id).bytes, key.generation,
                                       UUID(key.write_token).bytes, bytes.fromhex(key.scope.raw_binding_sha256),
                                       len(payload), zlib.crc32(descriptor, 0), zlib.crc32(payload, 0), bytes(28))
        raw = raw_header + descriptor + bytes(MAX_DESCRIPTOR_BYTES - len(descriptor)) + payload
        for name, item in (('context', context), ('raw', raw), ('header', PrefixHeader(raw_header, context)),
                           ('descriptor_bytes', descriptor), ('descriptor_sha256', hashlib.sha256(descriptor).hexdigest()),
                           ('payload_length', len(payload))):
            object.__setattr__(self, name, item)


@dataclass(frozen=True, init=False)
class OwnedSlot(_DescriptorAccess):
    """동일 owned bytes를 처음부터 재검사하고 원 FILLED와 대조한 layout-only 결과."""

    raw: bytes
    expected: ExpectedSlot
    header: PrefixHeader
    descriptor_bytes: bytes
    descriptor_sha256: str
    payload_length: int

    def __init__(self, raw, expected, optional_prefix=None):
        _exact(expected, ExpectedSlot)
        if type(raw) is not bytes or not METADATA_BYTES < len(raw) <= SLOT_BYTES:
            _fail('decoder requires exact owned used-slot bytes')
        header = PrefixHeader(raw[:HEADER_BYTES], expected.context)
        if optional_prefix is not None:
            _exact(optional_prefix, PrefixHeader)
            if optional_prefix.context != expected.context or optional_prefix.raw != header.raw:
                _fail('prefix and final owned header differ')
        if len(raw) != header.used_length:
            _fail('owned bytes length differs from declared used extent')
        info = expected.original_filled_info
        if header.payload_length != info.payload_length:
            _fail('header length differs from original FILLED')
        end = HEADER_BYTES + header.descriptor_length
        descriptor = raw[HEADER_BYTES:end]
        digest = hashlib.sha256(descriptor).hexdigest()
        if digest != info.descriptor_sha256:
            _fail('descriptor SHA differs from original FILLED')
        if zlib.crc32(descriptor, 0) != header.descriptor_crc32:
            _fail('descriptor CRC mismatch')
        if any(raw[end:METADATA_BYTES]):
            _fail('metadata slack must be zero')
        # 내부 CRC만 immutable bytes의 bounded view를 쓴다. 외부 view를 받거나 반환하지 않는다.
        if zlib.crc32(memoryview(raw)[METADATA_BYTES:], 0) != header.payload_crc32:
            _fail('payload CRC mismatch')
        own = _parse_descriptor(descriptor)
        _schema(own, expected.context, header.payload_length, completed=info.completed_us)
        for name, value in (('raw', raw), ('expected', expected), ('header', header),
                            ('descriptor_bytes', descriptor), ('descriptor_sha256', digest),
                            ('payload_length', header.payload_length)):
            object.__setattr__(self, name, value)


def prefix(header128bytes, context):
    """복사할 bounded 길이만 검사한다. 실제 lease/ready acquire는 caller의 책임이다."""
    return PrefixHeader(header128bytes, context)


def encode_owned(context, native_descriptor, payload_exact_bytes):
    """metadata+payload의 새 bytes≤2MiB를 만든다. payload[0]은 slot offset4096이다."""
    return SlotEncoding(context, native_descriptor, payload_exact_bytes)


def decode_owned(raw_exact_bytes, expected, optional_prefix=None):
    """원 bytes/원 FILLED를 대조한다. body CRC의 일관된 교체를 인증으로 구별하지 않는다."""
    return OwnedSlot(raw_exact_bytes, expected, optional_prefix)
