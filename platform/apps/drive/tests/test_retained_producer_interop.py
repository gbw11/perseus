"""보존된 실제 JP 생산 bytes의 C decoder/Scene 소비 상호운용만 검사한다.

차량·보정·primitive ID를 compiled lab로 바꾸지 않는다. 음성은 원 grant에서
lifecycle 두 필드만 바꾼 명시 fixture이며 실제 철회 producer 증거가 아니다.
"""
import base64
import hashlib
import json
import os
from pathlib import Path

import pytest
from test_scene_consumption_native import scene_probe, run_external_scene
from xenova_drive_world import decode, encode, schema


VECTOR_SHA256 = '76c7e26b927fb197a90726e7996a6f67d3cce620873a75bd1e54afd656bd849c'


def _varint(value):
    output = bytearray()
    while value > 127:
        output.append((value & 127) | 128)
        value >>= 7
    output.append(value)
    return bytes(output)


@pytest.mark.skipif(not os.environ.get('XENOVA_TEST_RETAINED_PRODUCER_VECTOR'),
    reason='explicit external JP producer vector not supplied')
def test_original_jp_retained_world_enters_actual_c_decode_and_scene_gate(scene_probe, tmp_path):
    path = Path(os.environ['XENOVA_TEST_RETAINED_PRODUCER_VECTOR'])
    raw = path.read_bytes()
    assert len(raw) == 14920 and hashlib.sha256(raw).hexdigest() == VECTOR_SHA256
    vector = json.loads(raw)
    original = {}
    for name in ('world', 'clock_trust', 'scene_grant', 'vehicle_sample', 'clock_probe'):
        row = vector[name]
        value = base64.b64decode(row['base64'], validate=True)
        assert len(value) == row['length']
        assert hashlib.sha256(value).hexdigest() == row['sha256']
        original[name] = value
    grant = decode(original['scene_grant'], schema.SceneGrant)
    assert vector['source']['vehicle'] == grant.vehicle == 'rc-car'
    assert vector['calibration']['body']['wheelbase'] == .20
    # Preserve the exact original standalone grant as the inner tag10 body.
    admitted = b'\x08\x02\x52' + _varint(len(original['scene_grant'])) + original['scene_grant']
    negative = schema.DriveLink(major=2)
    negative.scene_grant.CopyFrom(grant)
    negative.scene_grant.lifecycle_state = 3
    negative.scene_grant.lifecycle_revision += 1
    result = run_external_scene(scene_probe, tmp_path, original['world'], admitted,
        encode(negative), original['clock_trust'], grant.pi_host_boot, grant.gateway_instance,
        vector['times']['published_pi_interval'][1])
    assert not result.stderr
    print('JP_RETAINED_INTEROP ' + json.dumps(dict(vector_sha256=VECTOR_SHA256,
        original_payload_sha256={name: hashlib.sha256(value).hexdigest() for name, value in original.items()},
        c_scope='actual dw_decode/current Scene gate; no compiled planner/config substitution',
        negative_origin='explicit test fixture: lifecycle_state=3 and lifecycle_revision+1 only'), sort_keys=True))
