"""정적 SLOT chain의 격리. 실제 ready/write/read token이나 원장 예약을 실행하지 않는다."""

import os
import subprocess
import sys
import unittest


class SlotMessageIsolationTests(unittest.TestCase):
    def test_six_operations_preserve_original_records_without_io_or_clock(self):
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
from slot_message_fixtures import chain,slot_fields,packet
from xenova_vehicle.runtime.slot_messages import *
c=chain();r,g,f,o,q,a=(c[k] for k in ('REQUEST','GRANT','FILLED','OFFER','ACQUIRE','ACQUIRED'))
assert g.record.request is r.record
assert f.record.completed_us==o.record.content.completed_us==10000040
assert q.record.offer is o.record and a.record.read_request is q.record
assert a.record.read_request.offer.content is o.record.content
assert a.record.reserved_us==10000060 and q.record.request.issued_us==10000050
assert r.issued_us>r.context.bind.expires_us
for msg in c.values():
 copy=parse_slot_message(msg.packet,msg.context,msg.parent,limits=msg.limits)
 assert copy.raw is msg.packet.raw_bytes and copy.record==msg.record
 fields=msg.fields;fields['payload']['producer']='invalid'
 assert msg.fields!=fields
 for name in ('authorized','metric_ready','nn_ready','reserved','owner_exited','sent','highwater','outbox'):
  assert not hasattr(msg,name)
try:parse_slot_message(packet(slot_fields('REQUEST',r.context)),r.context,limits=None)
except SlotMessageError:pass
else:raise AssertionError('mandatory limits disappeared')
print('static SLOT6 isolation PASS; no actual registry/token/IO/READY authority')
"""
        result = subprocess.run([sys.executable, '-B', '-S', '-c', code], capture_output=True,
                                text=True, encoding='utf-8', timeout=15,
                                env={**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn('static SLOT6 isolation PASS', result.stdout)


if __name__ == '__main__':
    unittest.main()
