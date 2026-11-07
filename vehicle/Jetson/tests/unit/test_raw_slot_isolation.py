"""P0 codec의 순수 실행 격리. 실제 SDK/공유 메모리/finite·주행 검증은 하지 않는다."""

import os
import subprocess
import sys
import unittest


class RawSlotIsolationTests(unittest.TestCase):
    def test_encode_decode_needs_no_file_clock_uuid_generation_or_native_resource(self):
        code = """import builtins,io,os,time,uuid
original=builtins.__import__
def guarded(name,*args,**kwargs):
 if name.split('.')[0] in ('depthai','numpy','cv2','sqlite3','paho','socket','multiprocessing','mmap','ctypes','fcntl','threading','random','secrets'):
  raise AssertionError('forbidden import: '+name)
 return original(name,*args,**kwargs)
def deny(*args,**kwargs):raise AssertionError('explicit file/clock/UUID/sleep call')
builtins.__import__=guarded
builtins.open=io.open=os.open=deny
time.time=time.monotonic=time.monotonic_ns=time.perf_counter=time.perf_counter_ns=time.sleep=deny
uuid.uuid1=uuid.uuid4=deny
from raw_slot_fixtures import bundle,expected,raw_record
from xenova_vehicle.runtime.raw_slot import *
from raw_lease_fixtures import ledger,request,identifier,reader
from xenova_vehicle.runtime.raw_leases import FilledInfo,ReadRequest,ViewReturnFact,LeaseStatus
for present,configured in ((False,False),(False,True),(True,True)):
 c,d,p=bundle(nn_present=present,nn_configured=configured)
 e=SlotEncoding(c,d,p);h=PrefixHeader(e.raw[:128],c)
 o=OwnedSlot(e.raw,expected(e),h)
 assert h.used_length==len(e.raw) and o.payload_bytes()==p and o.descriptor()==d
 assert decode_owned(encode_owned(c,d,p).raw,expected(e),prefix(e.raw[:128],c)).raw==o.raw
 copy=o.descriptor();copy['streams']['rgb_video']['source_key'][1]='999'
 assert o.descriptor()==d
 # 활성 FP16 NaN도 이 codec의 범위를 바꾸지 않는다. parser 자격은 별도다.
 if present:
  body=bytearray(p);start=d['planes']['nn_output0']['offset']-4096
  body[start:start+2]=b'\\x00\\x7e'
  raw,exp=raw_record(c,d,bytes(body))
  assert OwnedSlot(raw,exp).payload_bytes()[start:start+2]==b'\\x00\\x7e'
 for name in ('metric_ready','capture_ready','nn_ready','authorized','drive_permitted'):
  assert not hasattr(o,name) and not hasattr(e,name) and not hasattr(h,name)
# 실제 token 동작은 하지 않고, 원 ledger key/FILLED가 codec까지 유지되는지만 결합한다.
l=ledger();g=l.reserve_write(0,request(500,issued=1000,expires=2000),identifier(501),now_us=1100).value
c=P0Context(g.key,dict(c.stream_ids),True);e=encode_owned(c,d,p)
info=FilledInfo(g.key,e.payload_length,e.descriptor_sha256,1600)
l.filled(info,now_us=1601)
r=l.acquire(ReadRequest(l.offer(0,now_us=1602),reader(),request(502,issued=1602,expires=1800)),identifier(503),now_us=1650).value
assert l.read_is_current(r,now_us=1650)
o=decode_owned(e.raw,ExpectedSlot(c,r.read_request.offer.content))
assert o.expected.original_filled_info is info and o.raw==e.raw
assert not l.read_is_current(r,now_us=1800)
assert l.release(ViewReturnFact(r,1801,1802),now_us=1803).status is LeaseStatus.APPLIED
try:encode_owned(c,d,memoryview(p))
except SlotCodecError:pass
else:raise AssertionError('borrowed input was accepted')
print('owned P0 layout-only isolation PASS; no actual memory/token/clock/finite authority')
"""
        result = subprocess.run([sys.executable, '-B', '-S', '-c', code], capture_output=True,
                                text=True, encoding='utf-8', timeout=15,
                                env={**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn('owned P0 layout-only isolation PASS', result.stdout)


if __name__ == '__main__':
    unittest.main()
