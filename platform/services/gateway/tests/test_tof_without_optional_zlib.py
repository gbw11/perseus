"""zlib 없는 설치에서도 기존 binascii로 원 ToF CRC를 검사하는 배포 회귀.

실제 모듈을 별도 Python 자식에서 import하고 정본393B와 조립 결과를 검사한다.
가상 clock/profile은 codec 입력 leaf이며 실제 ACK·장치·물리 FREE 증거가 아니다.
기존 pytest의 zlib 기반 독립 expected와 고정 vector는 변경하지 않는다.
"""
import json
from pathlib import Path
import subprocess
import sys

import pytest


CHILD = r'''
import builtins
import json
from pathlib import Path
import sys

root=Path(sys.argv[1]).resolve(strict=True)
case=sys.argv[2]
sys.path[:0]=[str(root/'platform/services/gateway'),str(root/'platform/apps/drive/world/python')]
original_import=builtins.__import__
blocked=[]
def without_zlib(name,*args,**kwargs):
    if name=='zlib' or name.startswith('zlib.'):
        blocked.append(name)
        raise ModuleNotFoundError('optional zlib deliberately unavailable',name='zlib')
    return original_import(name,*args,**kwargs)
for name in tuple(sys.modules):
    if name=='zlib' or name.startswith('zlib.'):
        del sys.modules[name]
builtins.__import__=without_zlib
try:
    __import__('zlib')
except ModuleNotFoundError:
    pass
else:
    raise AssertionError('deployment block was not effective')

from binascii import crc32
from xenova_drive_world import schema,decode
from xenova_gateway import tof_feedback3
from xenova_gateway.tof_feedback3 import TOF_COMMON3,TOF_FRAGMENT3,TofSelection,TofClock,TofInbox,TofAssembler
assert tof_feedback3.crc32 is crc32
assert crc32(b'123456789')==0xcbf43926
vectors=json.loads((root/'docs/contracts/vectors/gateway-feedback-3.json').read_bytes())
rows=[row for row in vectors['frames'] if row['name'].startswith('design_tof_fragment_')]
parts=[bytes.fromhex(row['payload_hex']) for row in rows]
first=TOF_FRAGMENT3.unpack(parts[0])
body=TOF_COMMON3.pack(*first[:14],first[18])+b''.join(part[145:] for part in parts)
expected=vectors['tof_design_only']
assert len(parts)==4 and all(len(part)==209 for part in parts)
assert len(body)==393 and body==bytes.fromhex(expected['whole_crc_body_hex'])
assert crc32(body)&0xffffffff==expected['sample_crc32']==first[19]
assert TOF_COMMON3.size==137 and TOF_FRAGMENT3.size==209
selection=TofSelection(((first[7],first[11].hex(),first[10]),),500000,20000,'rc-test-1',
    '00000000-0000-4000-8000-000000000001','00000000-0000-4000-8000-000000000004')
owner=TofAssembler(selection)
inbox=TofInbox()
received=first[3]+100000
clock=TofClock(first[5],first[6],99000,100000,received-10000,received+1000000,0)
if case=='whole-crc-corrupt':
    bad=bytearray(parts[-1]);bad[-1]^=1;parts[-1]=bytes(bad)
for part in parts:
    assert inbox.offer(part,received,clock)
loss,items=inbox.drain()
raw,revoked=owner.process(loss,items,(first[0],first[1],first[5],True),received)
if case=='original':
    assert len(raw)==1
    value=decode(raw[0],schema.TofObservation)
    assert value.source_crc32==expected['sample_crc32']
    assert value.source_sample==first[2] and value.source_us==first[3]
    assert owner.sensors[first[7]]['latest']['body']==body
    assert value.complete and value.sensor_instance==first[8] and value.sensor_generation==first[9]
else:
    assert case=='whole-crc-corrupt' and raw==()
    assert owner.sensors[first[7]]['partial'] is None and owner.sensors[first[7]]['latest'] is None
    assert owner.counts['rejected']==1 and owner.counts['completed']==0
assert 'zlib' not in sys.modules
assert blocked and blocked[0]=='zlib'
print(json.dumps(dict(case=case,body_bytes=len(body),crc32=crc32(body)&0xffffffff,
    original_sample=first[2],original_source_us=first[3],zlib_blocked=True,
    runtime_uses_existing_binascii=True,observations=len(raw))))
'''


@pytest.mark.parametrize('case',['original','whole-crc-corrupt'])
def test_original_tof_import_and_whole_sample_crc_work_without_optional_zlib(case):
    root=Path(__file__).resolve().parents[4]
    child=subprocess.run([sys.executable,'-I','-B','-c',CHILD,str(root),case],
        capture_output=True,text=True,timeout=30,check=False)
    assert child.returncode==0,child.stdout[-4096:]+'\n'+child.stderr[-8192:]
    value=json.loads(child.stdout)
    assert value['case']==case and value['body_bytes']==393
    assert value['zlib_blocked'] and value['runtime_uses_existing_binascii']
    assert value['observations']==(1 if case=='original' else 0)
