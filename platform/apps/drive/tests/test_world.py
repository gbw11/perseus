"""Python producer -> real nanopb product decoder; independent pose expectations."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

HOME=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HOME/'world/python'))
from world_codec import schema,encode,decode
spec=importlib.util.spec_from_file_location('owned_world_fixture',HOME/'examples/world_fixture.py')
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)

@pytest.fixture(scope='module')
def probe(tmp_path_factory):
    build=tmp_path_factory.mktemp('world-native');binary=build/'world-probe'
    world=HOME/'world'
    sources=[world/'world.c',world/'generated/world.pb.c',*(world/'nanopb'/name for name in ('pb_common.c','pb_decode.c','pb_encode.c')),HOME/'tests/world_probe.c']
    args=['cc','-std=c11','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-DTEST_PRIMITIVE_SHA="'+fixture.PRIMITIVE+'"',
        '-I'+str(world),'-I'+str(world/'nanopb'),'-I'+str(world/'generated'),*[str(p) for p in sources],'-lm','-o',str(binary)]
    subprocess.run(args,check=True,capture_output=True,text=True)
    return binary,build

def product(probe,data):
    binary,build=probe;path=build/'input.bin';path.write_bytes(data)
    result=subprocess.run([str(binary),str(path)],check=True,capture_output=True,text=True)
    return json.loads(result.stdout)

def test_normal_and_maximum_python_to_nanopb_and_pool_pose(probe):
    for height in (32,64):
        value=fixture.world(height=height)
        wire=encode(value)
        assert decode(wire)==value
        result=product(probe,wire)
        assert result['result']=='OK' and result['bytes']<32768
        assert result['pool_size']<120000 and result['odom_size']<80000

def test_frozen_protoc_golden_to_python_and_nanopb(probe):
    vector=json.loads((HOME.parents[2]/'docs/contracts/drive-world/vectors/world-4x4.json').read_text())
    wire=bytes.fromhex(vector['wire_hex'])
    import hashlib
    assert hashlib.sha256(wire).hexdigest()==vector['sha256']
    message=decode(wire)
    assert (message.width,message.height,message.sequence)==(4,4,2)
    assert message.occupancy==bytes(16) and message.cell_flags==bytes([3])*16
    assert message.age_le32==b'\x80\xee\x36\x00'*16
    assert encode(message)==wire
    # 보존한 protoc golden은 당시 primitive revision이다. 새 생성기 산출물의
    # 호환성을 임의 허용하지 않고 현재 asset 참조만 바꾼 정상 입력과 짝지어 확인한다.
    assert product(probe,wire)['result']==('OK' if message.primitive==fixture.PRIMITIVE else 'CONFIGURATION')
    message.primitive=fixture.PRIMITIVE
    assert product(probe,encode(message))['result']=='OK'
    with pytest.raises(ValueError):decode(wire+b'\x08\x01')
    assert product(probe,wire+b'\x08\x01')['result']=='DECODE'


@pytest.mark.parametrize('field,value,reason',[
    ('major',2,'VERSION'),('required_features',2,'VERSION'),('complete',False,'ARRAY'),
    ('truncated',True,'ARRAY'),('width',65,'ARRAY'),('occupancy',b'','ARRAY'),
    ('mapper_boot',fixture.INSTANCE,'SOURCE'),('frame_epoch',fixture.INSTANCE,'EPOCH'),
    ('configuration','wrong','CONFIGURATION'),('resolution_m',float('nan'),'GEOMETRY'),
    ('clock_epoch',fixture.INSTANCE,'TIME'),('published_us',4000000,'TIME')])
def test_semantic_errors_do_not_become_default_values(probe,field,value,reason):
    message=fixture.world();setattr(message,field,value)
    assert product(probe,message.SerializeToString())['result']==reason

def test_required_presence_partial_duplicate_unknown_and_oversize(probe):
    message=fixture.world();message.ClearField('height')
    assert product(probe,message.SerializePartialToString())['result']=='DECODE'
    normal=encode(fixture.world())
    assert product(probe,normal[:-7])['result']=='DECODE'
    assert product(probe,normal+b'\x08\x01')['result']=='DECODE'
    assert product(probe,b'x'*32769)['result']=='TOO_LARGE'
    # Compatible optional unknown field is ignored; mandatory semantics must
    # declare required_features and fail above.
    assert product(probe,normal+b'\xf8\x07\x01')['result']=='OK'
    # An embedded NUL must not make C's strcmp see only a trusted prefix.
    value=fixture.world();value.vehicle='rc-test-1\x00extra'
    assert product(probe,value.SerializeToString())['result']=='DECODE'

def test_producer_rejects_nonfinite_and_missing_arrays():
    value=fixture.world();value.reference[0].x=float('inf')
    with pytest.raises(ValueError):encode(value)
    value=fixture.world();value.cell_flags=b''
    with pytest.raises(ValueError):encode(value)


def test_preferred_road_feature_is_explicit_and_never_supplies_free_cells(probe):
    from xenova_drive_world import mark_road
    value=fixture.world()
    original=(value.occupancy,value.age_le32,bytes(value.cell_flags))
    left=[schema.Point(x=p.x,y=1.96) for p in value.reference]
    right=[schema.Point(x=p.x,y=1.24) for p in value.reference]
    mark_road(value,left,right)
    assert value.required_features==1 and value.minor==1
    assert value.occupancy==original[0] and value.age_le32==original[1]
    assert all((a&7)==b for a,b in zip(value.cell_flags,original[2]))
    assert product(probe,encode(value))['result']=='OK'
    value.required_features=0
    with pytest.raises(ValueError,match='cell encoding'):encode(value)
    assert product(probe,value.SerializeToString())['result']=='ARRAY'
    value.required_features=1;value.cell_flags=bytes([3])*len(value.cell_flags)
    with pytest.raises(ValueError,match='preferred road'):encode(value)
    assert product(probe,value.SerializeToString())['result']=='ARRAY'


def test_road_shape_rejects_crossing_or_wrong_side_then_accepts_recovery(probe):
    value=fixture.world();value.left_boundary[3].y=-.2
    assert product(probe,encode(value))['result']=='GEOMETRY'
    value=fixture.world();value.left_boundary[3].CopyFrom(value.left_boundary[2])
    assert product(probe,encode(value))['result']=='GEOMETRY'
    assert product(probe,encode(fixture.world()))['result']=='OK'
