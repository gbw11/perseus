"""순수 TX 준비·합성 STOP handoff를 격리한다. 실제 send/exit/lease/READY 수락은 아니다."""

import os
import subprocess
import sys
import unittest


class TxOutboxIsolationTests(unittest.TestCase):
    def test_draft_queue_prepare_and_fake_handoff_need_no_native_io_clock_or_process(self):
        code = """import builtins,io,os,time
original=builtins.__import__
def guarded(name,*args,**kwargs):
 if name.split('.')[0] in ('depthai','numpy','cv2','sqlite3','paho','socket','multiprocessing'):
  raise AssertionError('forbidden import: '+name)
 return original(name,*args,**kwargs)
def deny(*args,**kwargs):raise AssertionError('explicit clock/file/sleep call')
builtins.__import__=guarded
builtins.open=io.open=os.open=deny
time.time=time.monotonic=time.monotonic_ns=time.perf_counter=time.perf_counter_ns=time.sleep=deny
from lifecycle_draft_fixtures import draft,request
from lifecycle_message_fixtures import peer,packet
from xenova_vehicle.runtime.lifecycle_messages import LifecycleReceiver,LifecycleMessageError
import xenova_vehicle.runtime.tx_outbox as tx
import xenova_vehicle.runtime.local_envelope as outer
encoder=tx.encode_structure;outer_encoder=outer.encode_structure
tx.encode_structure=outer.encode_structure=deny
p=peer();o=tx.TxOutbox(p,'S')
h1=draft('HEARTBEAT',request_id=request(10));h2=draft('HEARTBEAT',request_id=request(11))
assert 'message_sequence' not in h1.fields and len(h1.fields)==11
assert o.put(h1,now_us=100) is tx.TxPutResult.ACCEPTED
assert o.encoded_held_bytes==0 and o.snapshot.next_sequence==1
tx.encode_structure=encoder;outer.encode_structure=outer_encoder
ready=o.prepare(now_us=110);first=ready.handle;original_bytes=first.raw_bytes
assert ready.status is tx.TxStatus.PREPARED_NEW
assert o.put(h2,now_us=110) is tx.TxPutResult.ACCEPTED
assert o.put(h1,now_us=111) is tx.TxPutResult.DUPLICATE_NO_EFFECT
assert o.snapshot.heartbeat is h2
for _ in range(1000):
 observed=o.prepare(now_us=120)
 assert observed.handle is first and observed.handle.raw_bytes==original_bytes and not observed.drops
expired=o.prepare(now_us=200)
assert expired.status is tx.TxStatus.PENDING_EXPIRED and expired.handle is first
drop=o.abandon(first,cause=tx.DiscardCause.EXPIRED)
assert drop.handle is first and drop.draft is h1 and o.snapshot.next_sequence==2
assert not o.finish(first) and o.abandon(first,cause=tx.DiscardCause.CANCELLED) is None
all_expired=o.prepare(now_us=200)
assert all_expired.status is tx.TxStatus.EMPTY and len(all_expired.drops)==1
assert o.snapshot.next_sequence==2
for name in ('authorized','drive_permitted','process_exited','leases_free','ready'):
 assert not hasattr(o.snapshot,name) and not hasattr(all_expired,name)
sender=tx.TxOutbox(p,'A');s=LifecycleReceiver(p,'S');child=LifecycleReceiver(p,'A')
stop=draft('STOP',request_id=request(20))
sender.put(stop,now_us=100)
assert s.snapshot.pending_stop is None
prepared=sender.prepare(now_us=110);handle=prepared.handle
assert s.register_stop_intent(handle.packet,now_us=110) and s.snapshot.highwater==0
assert child.receive(handle.packet,now_us=120).message.kind=='STOP'
report=packet('STOPPED',issued=125,request_id=stop.request_id)
try:s.receive(report,now_us=130)
except LifecycleMessageError:pass
else:raise AssertionError('enqueue/prepare/intent invented full-send fact')
assert s.record_first_sent(handle.packet,120,now_us=131)
assert sender.finish(handle) and s.receive(report,now_us=140).message.kind=='STOPPED'
assert s.snapshot.pending_stop is not None
print('unsequenced draft and synthetic TX isolation PASS; no actual send/exit/lease grant')
"""
        result = subprocess.run([sys.executable, '-B', '-S', '-c', code], capture_output=True,
                                text=True, encoding='utf-8', timeout=10,
                                env={**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn('synthetic TX isolation PASS', result.stdout)


if __name__ == '__main__':
    unittest.main()
