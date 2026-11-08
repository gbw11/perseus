"""초기 P0 RawBinding의 canonical 값과 독립 승인 참조. 자산 파일·장치·등록은 열지 않는다.

model은 compiled blob 원 bytes, parser/graph/format/calibration은 각각 출판한
immutable manifest 원 bytes의 SHA 참조다. 참조의 문법이나 caller profile 일치가
실제 파일 존재·보정·capture/metric 준비를 증명하지 않는다. 기존 진단 label은 별개다.
"""

from dataclasses import dataclass
import hashlib
import json
import re
from uuid import UUID


BINDING_FIELDS = frozenset(('service_instance', 'acquisition_worker', 'pipeline_instance',
    'capture_epoch', 'stream_instances', 'sdk_bridge_epoch', 'device_id', 'model_id',
    'parser_id', 'graph_profile_sha256', 'calibration_bundle', 'format_table_sha256', 'clock_epoch'))
UUID_FIELDS = ('service_instance', 'acquisition_worker', 'pipeline_instance', 'capture_epoch',
               'sdk_bridge_epoch', 'clock_epoch')
REF_FIELDS = ('model_id', 'parser_id', 'graph_profile_sha256', 'format_table_sha256', 'calibration_bundle')
BASE_STREAMS = ('rgb_video', 'depth_right')
NN_STREAMS = ('nn_input0', 'nn_output0')


class RawBindingError(ValueError):
    """bounded P0 값이나 caller가 독립 공급한 참조가 맞지 않는다."""


def _fail(message):
    raise RawBindingError(message)


def _exact(value, expected):
    if type(value) is not expected:
        _fail('expected exact ' + expected.__name__)


def _uuid(value):
    if type(value) is not str or len(value) != 36:
        _fail('expected canonical UUID')
    try:
        normalized = str(UUID(value))
    except ValueError as error:
        raise RawBindingError('invalid UUID') from error
    if normalized != value:
        _fail('expected canonical lowercase UUID')


def _sha(value):
    if type(value) is not str or len(value) != 64 or re.fullmatch('[0-9a-f]{64}', value) is None:
        _fail('expected lowercase SHA256 reference')


def _device(value):
    if type(value) is not str or re.fullmatch('[A-Za-z0-9_-]{1,64}', value) is None:
        _fail('expected case-preserved approved device ID grammar')


def _refs(model, parser, graph, format_table, calibration, nn_enabled):
    _exact(nn_enabled, bool)
    _sha(graph)
    _sha(format_table)
    if calibration is not None:
        _sha(calibration)
    if nn_enabled:
        _sha(model)
        _sha(parser)
    elif model is not None or parser is not None:
        _fail('NN-absent profile requires null model/parser references')


def _copy_body(fields):
    """root13/stream5·깊이2·64출현의 소유 사본을 만든다. 임의 hook은 실행하지 않는다."""
    nodes = 0
    active = set()

    def visit(value, parent_depth):
        nonlocal nodes
        nodes += 1
        if nodes > 64:
            _fail('binding exceeds 64 key/value/container nodes')
        kind = type(value)
        if kind is str:
            if len(value) > 64 or any(ord(c) > 127 for c in value):
                _fail('binding string/key exceeds ASCII64')
            return value
        if value is None:
            return None
        if kind is not dict:
            _fail('binding accepts exact dict/str/None only')
        depth = parent_depth + 1
        if depth > 2 or len(value) > (13 if depth == 1 else 5):
            _fail('binding exceeds container depth/width')
        identity = id(value)
        if identity in active:
            _fail('cyclic binding')
        active.add(identity)
        try:
            own = {}
            for key, child in value.items():
                if type(key) is not str:
                    _fail('binding key must be an exact string')
                own_key = visit(key, depth)
                own[own_key] = visit(child, depth)
            return own
        finally:
            active.remove(identity)

    return visit(fields, 0)


def _validate_body(value):
    if type(value) is not dict or set(value) != BINDING_FIELDS:
        _fail('RawBinding requires the exact thirteen fields')
    for name in UUID_FIELDS:
        _uuid(value[name])
    _device(value['device_id'])
    streams = value['stream_instances']
    if type(streams) is not dict:
        _fail('expected named stream object')
    names = set(streams)
    nn_enabled = 'nn_input0' in names
    expected = set(BASE_STREAMS) | (set(NN_STREAMS) if nn_enabled else set())
    if 'jpeg' in names:
        expected.add('jpeg')
    if names != expected:
        _fail('P0 requires RGB/depth, optional NN pair and optional JPEG; IMU unsupported')
    for identity in streams.values():
        _uuid(identity)
    if len(set(streams.values())) != len(streams):
        _fail('selected named streams must have distinct UUIDs')
    _refs(*(value[name] for name in REF_FIELDS), nn_enabled)


@dataclass(frozen=True, init=False)
class RawBinding:
    """immutable canonical body. 원 outer packet의 bytes/hash를 대신하지 않는다."""

    canonical_bytes: bytes
    sha256: str

    def __init__(self, fields):
        own = _copy_body(fields)
        _validate_body(own)
        raw = json.dumps(own, sort_keys=True, ensure_ascii=True, separators=(',', ':'),
                         allow_nan=False).encode('ascii')
        if len(raw) > 2048:
            _fail('canonical RawBinding exceeds 2048 bytes')
        object.__setattr__(self, 'canonical_bytes', raw)
        object.__setattr__(self, 'sha256', hashlib.sha256(raw).hexdigest())

    @property
    def fields(self):
        """새 dict/stream map 사본. 검증된 ASCII str만 JSON parser에 전달한다."""
        return json.loads(self.canonical_bytes.decode('ascii'))


@dataclass(frozen=True)
class RawBindingProfile:
    """body와 독립적으로 승인한 참조의 값. 파일 검증이나 실제 승인자를 생성하지 않는다."""

    device_id: str
    clock_epoch: str
    model_id: str | None
    parser_id: str | None
    graph_profile_sha256: str
    format_table_sha256: str
    calibration_bundle: str | None
    nn_enabled: bool
    jpeg_enabled: bool

    def __post_init__(self):
        _device(self.device_id)
        _uuid(self.clock_epoch)
        _exact(self.jpeg_enabled, bool)
        _refs(*(getattr(self, name) for name in REF_FIELDS), self.nn_enabled)

    @property
    def stream_names(self):
        return BASE_STREAMS + (NN_STREAMS if self.nn_enabled else ()) + (('jpeg',) if self.jpeg_enabled else ())

    @property
    def refs(self):
        return {name: getattr(self, name) for name in REF_FIELDS}

    def check(self, binding):
        """등록된 ref/clock/device/stream 선택과 exact 대조한다. registry는 바꾸지 않는다."""
        _exact(binding, RawBinding)
        value = binding.fields
        expected = dict(self.refs, device_id=self.device_id, clock_epoch=self.clock_epoch)
        if any(value[name] != wanted for name, wanted in expected.items()):
            _fail('binding differs from independent approved references/device/clock')
        if set(value['stream_instances']) != set(self.stream_names):
            _fail('binding stream selection differs from independent profile')
