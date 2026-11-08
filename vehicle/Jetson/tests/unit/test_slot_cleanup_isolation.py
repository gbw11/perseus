"""반환 형식·원장의 격리 검사. 실제 view/token/peer/OS 종료를 관측하지 않는다."""

import os
import subprocess
import sys
import unittest


class SlotCleanupIsolationTests(unittest.TestCase):
    def test_static_release_and_two_return_kinds_need_no_io_clock_or_sdk(self):
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
from slot_cleanup_fixtures import make
from raw_lease_fixtures import ledger,write_ready,read_lease
from xenova_vehicle.runtime.slot_cleanup import parse_release_message
from xenova_vehicle.runtime.raw_leases import RemoteReturnStatement,ViewReturnFact,LeaseStatus
m=make()
assert m.record.lease is m.parent.record and m.context is m.parent.context
assert m.issued_us>m.parent.expires_us
assert parse_release_message(m.packet,m.parent,limits=m.limits).raw is m.packet.raw_bytes
copy=m.payload;copy['op']='REVOKE';assert m.payload['op']=='RELEASE'
for remote_first in (False,True):
 v=ledger();write_ready(v);lease=read_lease(v)
 l=ViewReturnFact(lease,390,400);r=RemoteReturnStatement(lease,400)
 first=(lambda:v.release_remote(r,now_us=500)) if remote_first else (lambda:v.release(l,now_us=500))
 second=(lambda:v.release(l,now_us=500)) if remote_first else (lambda:v.release_remote(r,now_us=500))
 assert first().status is LeaseStatus.APPLIED
 assert second().status is LeaseStatus.CLEANUP_CORROBORATED
 before=v.snapshot
 assert first().status is second().status is LeaseStatus.DUPLICATE_NO_EFFECT
 assert v.snapshot==before
 for name in ('shm','semaphore','actual_exit','metric_ready','nn_ready','authorized'):
  assert not hasattr(v.snapshot,name)
print('static RELEASE/local-remote bookkeeping isolation PASS; no SDK/IO/token/exit acceptance')
"""
        result = subprocess.run([sys.executable, '-B', '-S', '-c', code], capture_output=True,
                                text=True, encoding='utf-8', timeout=15,
                                env={**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn('bookkeeping isolation PASS', result.stdout)


if __name__ == '__main__':
    unittest.main()
