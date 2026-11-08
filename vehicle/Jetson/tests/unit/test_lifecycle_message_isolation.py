"""초기 lifecycle의 순수 합성 수신을 격리 실행한다. 실제 send/exit/회수 시험은 아니다."""

import os
import subprocess
import sys
import unittest


class LifecycleMessageIsolationTests(unittest.TestCase):
    def test_synthetic_lifecycle_needs_no_io_native_clock_or_process(self):
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
from lifecycle_message_fixtures import packet,peer
from xenova_vehicle.runtime.lifecycle_messages import LifecycleReceiver,RxDisposition,LifecycleMessageError
p=peer();s=LifecycleReceiver(p,'S');child=LifecycleReceiver(p,'A')
hello=packet('HELLO',sequence=3)
result=s.receive(hello,now_us=100)
assert result.disposition is RxDisposition.ACCEPTED_NEW and result.message.kind=='HELLO'
duplicate=s.receive(hello,now_us=101)
assert duplicate.disposition is RxDisposition.DUPLICATE_NO_EFFECT and duplicate.message is None
hb=packet('HEARTBEAT',sequence=5,payload=dict(worker_state='STREAMING',last_progress_sequence='2',last_progress_j_us='100'))
statement=s.receive(hb,now_us=110).message
for name in ('authorized','ready','nn_ready','drive_permitted','process_exited','leases_free'):
 assert not hasattr(statement,name) and not hasattr(s.snapshot,name)
stop=packet('STOP',sequence=8)
before=s.snapshot
assert s.register_stop_intent(stop,now_us=110)
assert not s.register_stop_intent(stop,now_us=111)
assert s.snapshot.highwater==before.highwater and s.snapshot.hello_seen==before.hello_seen
assert child.receive(stop,now_us=120).message.kind=='STOP'
assert child.snapshot.pending_stop.first_sent_us is None
report=packet('STOPPED',sequence=9,issued=125)
try:s.receive(report,now_us=130)
except LifecycleMessageError:pass
else:raise AssertionError('intent was treated as send fact')
assert s.record_first_sent(stop,120,now_us=131)
assert not s.record_first_sent(stop,120,now_us=132)
result=s.receive(report,now_us=140)
assert result.message.kind=='STOPPED' and result.message.payload['device_closed'] is True
assert s.snapshot.pending_stop.first_sent_us==120 and s.snapshot.stopped_report is result.message
assert s.receive(report,now_us=141).message is None
assert s.snapshot.highwater==9 and s.snapshot.pending_stop is not None
fresh=LifecycleReceiver(p,'S')
assert fresh.register_stop_intent(stop,now_us=110)
assert fresh.record_first_sent(stop,120,now_us=131)
assert fresh.receive(report,now_us=140).message.kind=='STOPPED'
assert not fresh.snapshot.hello_seen
external=result.message.payload;external['leases_released']=False
assert s.snapshot.stopped_report.payload['leases_released'] is True
print('synthetic lifecycle isolation PASS; reports are not send/exit/lease authority')
"""
        result = subprocess.run([sys.executable, '-B', '-S', '-c', code], capture_output=True,
                                text=True, encoding='utf-8', timeout=10,
                                env={**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn('synthetic lifecycle isolation PASS', result.stdout)


if __name__ == '__main__':
    unittest.main()
