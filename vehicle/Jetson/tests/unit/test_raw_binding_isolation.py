"""RawBinding/정적 ACK의 IO·clock 격리. 실제 등록·송신·준비를 실행하지 않는다."""

import os
import subprocess
import sys
import unittest


class RawBindingIsolationTests(unittest.TestCase):
    def test_value_bind_ack_need_no_io_clock_uuid_generation_or_registry(self):
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
from raw_binding_fixtures import context,bind_packet,ack_packet
from xenova_vehicle.runtime.raw_binding_messages import *
for role in ('A','M'):
 for nn,jpeg in ((False,False),(False,True),(True,False),(True,True)):
  ctx=context(role,nn_enabled=nn,jpeg_enabled=jpeg)
  bind=parse_raw_bind(bind_packet(ctx),ctx)
  ack=parse_raw_bind_ack(ack_packet(bind,sequence=1),bind)
  assert ack.accepted and ack.raw is ack.packet.raw_bytes
  assert ack.original_bind is bind and ack.binding is bind.binding
  assert bind.fields['message_sequence']=='10' and ack.fields['message_sequence']=='1'
  copy=bind.binding.fields;copy['stream_instances']['rgb_video']='invalid'
  assert bind.binding.fields!=copy
  payload=ack.payload;payload['accepted']=False
  assert ack.payload['accepted'] is True
  refused=RawBindAck(ack_packet(bind,accepted=False,state='FAILED'),bind)
  assert not refused.accepted and refused.reason=='UNSUPPORTED_PROFILE'
  for result in (bind,ack,refused,bind.binding,ctx.profile):
   for name in ('nn_ready','capture_ready','metric_ready','drive_permitted','registered','sent','highwater','outbox'):
    assert not hasattr(result,name)
print('static RAW binding isolation PASS; no actual registration/send/READY authority')
"""
        result = subprocess.run([sys.executable, '-B', '-S', '-c', code], capture_output=True,
                                text=True, encoding='utf-8', timeout=15,
                                env={**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn('static RAW binding isolation PASS', result.stdout)


if __name__ == '__main__':
    unittest.main()
