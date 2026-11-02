"""Gateway 전용 작은 로컬 호출 봉투. client import에는 서버·MQTT가 필요 없다."""
import json
import math
import struct
from uuid import UUID
import time
from uuid import uuid4

VERSION = 1
ENDPOINT = '/run/xenova-gateway/control.sock'
MAX_BYTES = 8192
HEADER = struct.Struct('!I')


def uuid(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError('canonical UUID required')
    return value


def request_key(value):
    """Legacy UUID or host-bound monotonic issuance key; never mint on replay."""
    if isinstance(value, str) and value.startswith('g1:'):
        parts = value.split(':')
        if len(parts) != 4 or len(value) > 100:
            raise ValueError('Gateway request key invalid')
        uuid(parts[1]); uuid(parts[3])
        if not parts[2].isascii() or not parts[2].isdigit() or str(int(parts[2])) != parts[2]:
            raise ValueError('canonical issuance time required')
        integer(int(parts[2]))
        return parts[1], int(parts[2])
    uuid(value)
    return None


def new_request_id(host_boot_id):
    """Call once before first submit, persist with the original request/budget."""
    return f'g1:{uuid(host_boot_id)}:{time.monotonic_ns() // 1000000}:{uuid4()}'


def integer(value, *, minimum=0, maximum=2**63-1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('bounded integer required')
    return value


def seconds(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 10:
        raise ValueError('call budget must be finite, in (0,10] seconds')
    return value


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def decode(data):
    if len(data) > MAX_BYTES:
        raise ValueError('message size limit')
    try:
        value = json.loads(data.decode('utf-8'), object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
    except RecursionError as error:
        raise ValueError('JSON nesting limit') from error
    if not isinstance(value, dict):
        raise ValueError('JSON object required')
    return value


def encode(value):
    data = json.dumps(value, separators=(',', ':'), allow_nan=False).encode()
    if len(data) > MAX_BYTES:
        raise ValueError('message size limit')
    return HEADER.pack(len(data)) + data


def request(value):
    if (set(value) - {'version', 'action', 'request_id', 'spec'} or
            type(value.get('version')) is not int or value['version'] != VERSION):
        raise ValueError('unsupported Gateway call envelope')
    action = value.get('action')
    if action not in ('context', 'history', 'submit', 'get', 'commit', 'cancel', 'finish'):
        raise ValueError('unknown Gateway action')
    if action not in ('context', 'history'):
        request_key(value.get('request_id'))
    elif set(value) != {'version', 'action'}:
        raise ValueError('context accepts no request arguments')
    spec = value.get('spec', {})
    if not isinstance(spec, dict):
        raise ValueError('spec object required')
    if action in ('get', 'commit') and spec:
        raise ValueError('action accepts no spec')
    return value
