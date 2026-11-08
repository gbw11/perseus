"""소유권 원장의 합성 실행 격리. 실제 token/view/SHM/OS 완료 사실은 시험하지 않는다."""

import os
import subprocess
import sys
import unittest


class RawLeaseIsolationTests(unittest.TestCase):
    def test_normal_cleanup_replacement_and_fence_need_no_native_io_clock_or_resources(self):
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
from raw_lease_fixtures import ledger,write_ready,read_lease,request,identifier,reader
from xenova_vehicle.runtime.raw_leases import *
l=ledger();grant,info=write_ready(l);r=read_lease(l)
assert grant.key.scope.writer.channel_epoch!=r.key.reader.channel_epoch
assert l.snapshot.slots[0].state is SlotState.IN_USE
assert not l.read_is_current(r,now_us=145) and l.read_is_current(r,now_us=150)
assert not l.read_is_current(r,now_us=300)
assert l.snapshot.slots[0].state is SlotState.IN_USE
ended=ViewReturnFact(r,301,302)
assert l.release(ended,now_us=310).status is LeaseStatus.APPLIED
assert l.release(ended,now_us=311).status is LeaseStatus.DUPLICATE_NO_EFFECT
assert l.snapshot.slots[0].state is SlotState.FREE
g2,i2=write_ready(l,request_number=50,token_number=51,issued=310,expires=500,granted=320,completed=330,received=340)
ticket=l.begin_replace(0,i2.key,request(60,issued=340,expires=500),identifier(61),now_us=350).value
assert l.snapshot.slots[0].state is SlotState.READY and l.snapshot.slots[0].pending.ticket is ticket
fact=ReplacementSyncFact(ticket,360,361,362)
replacement=l.complete_replace(ticket,fact,now_us=370)
assert replacement.status is LeaseStatus.APPLIED and replacement.value.key.generation==3
assert not l.write_is_current(replacement.value,now_us=369)
assert l.write_is_current(replacement.value,now_us=370)
assert l.complete_replace(ticket,fact,now_us=371).status is LeaseStatus.DUPLICATE_NO_EFFECT
i3=FilledInfo(replacement.value.key,960000,'d'*64,380)
l.filled(i3,now_us=390)
# 완료된 WRITE의500 상한을 독립 read의 원 기한700에 적용하지 않는다.
r2=read_lease(l,request_number=70,token_number=71,issued=510,expires=700,reserved=520)
assert l.read_is_current(r2,now_us=521)
old=l.snapshot
metric=ReadRequest(Offer(i3),reader(),request(72,issued=520,expires=650),purpose='METRIC_MAP')
try:l.acquire(metric,identifier(73),now_us=530)
except UnsupportedPurpose:pass
else:raise AssertionError('metric pre-read contract was invented')
assert l.snapshot==old
l.fence(now_us=701,retired=True)
assert l.snapshot.slots[0].state is SlotState.IN_USE and not l.read_is_current(r2,now_us=702)
assert l.release(ViewReturnFact(r2,702,703),now_us=704).status is LeaseStatus.APPLIED
assert l.snapshot.fenced and l.snapshot.retired and l.snapshot.slots[0].state is SlotState.FREE
try:l.reserve_write(0,request(80,issued=705,expires=800),identifier(81),now_us=710)
except LeaseLedgerError:pass
else:raise AssertionError('cleanup FREE revived the retired arena')
partial=ledger();_,before=write_ready(partial)
t=partial.begin_replace(0,before.key,request(90,issued=130,expires=250),identifier(91),now_us=140).value
f=ReplacementSyncFact(t,None,None,150)
assert partial.complete_replace(t,f,now_us=151).status is LeaseStatus.REPLACEMENT_FAILED
assert partial.snapshot.fenced and partial.snapshot.fault is ReplacementFault.PARTIAL
assert partial.snapshot.slots[0].state is SlotState.READY and partial.snapshot.slots[0].pending.fact is f
assert partial.complete_replace(t,f,now_us=300).status is LeaseStatus.DUPLICATE_NO_EFFECT
for name in ('authorized','drive_permitted','actual_token_baseline','owner_exited','metric_ready'):
 assert not hasattr(partial.snapshot,name)
print('synthetic raw ledger isolation PASS; no actual memory/token/exit authority')
"""
        result = subprocess.run([sys.executable, '-B', '-S', '-c', code], capture_output=True,
                                text=True, encoding='utf-8', timeout=10,
                                env={**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn('synthetic raw ledger isolation PASS', result.stdout)


if __name__ == '__main__':
    unittest.main()
