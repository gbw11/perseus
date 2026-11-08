"""정책 import와 개별 사실의 양성 실행이 I/O/native 없이 끝나는지 검사한다."""

import os
import subprocess
import sys
import unittest


class LifecycleIsolationTests(unittest.TestCase):
    def test_no_native_clock_file_sleep_or_process_import_during_policy_use(self):
        code = """import builtins,io,os,sys,time
original=builtins.__import__
def guarded(name,*args,**kwargs):
 if name.split('.')[0] in ('depthai','numpy','cv2','sqlite3','paho','socket','multiprocessing'):
  raise AssertionError('forbidden import: '+name)
 return original(name,*args,**kwargs)
def deny(*args,**kwargs):
 raise AssertionError('policy called clock/file/sleep')
builtins.__import__=guarded
builtins.open=io.open=os.open=deny
time.time=time.monotonic=time.monotonic_ns=time.perf_counter=time.perf_counter_ns=time.sleep=deny
from xenova_vehicle.runtime.lifecycle_policy import (
 CaptureScope,SourceStamp,CapturePairEvidence,CaptureWarmup,AbsoluteDeadline,
 ParserContext,BindWatch,ParserWarmup,ParserEvidence,TIMING_US,ROLE_STATES)
def identity(n):return '00000000-0000-0000-0000-'+format(n,'012x')
scope=CaptureScope('a'*64,identity(1),identity(2),identity(3),identity(4),identity(5))
capture=CaptureWarmup(scope,0,0,AbsoluteDeadline.after(0,15000000,now_us=0),now_us=0)
for n in range(1,31):
 rgb=SourceStamp(scope.rgb_stream,n,n,n,n*1000)
 depth=SourceStamp(scope.depth_stream,n+1000,n+1,n+1,n*1000)
 assert capture.observe(CapturePairEvidence(scope,rgb,depth,n*1000,True),now_us=n*1000)
assert capture.ready and capture.count==30 and capture.ready_us==30000
context=ParserContext(scope,identity(6),identity(7),'pinned-model','pinned-parser')
parser=ParserWarmup(context,'REQUIRED')
assert parser.admit_capture(capture,now_us=30000)
binding=BindWatch('M',context.mapper_worker,scope.raw_binding_sha256,context.bind_request_id,40000,now_us=40000)
assert binding.sent(41000,now_us=41000)
assert binding.ack(context.mapper_worker,scope.raw_binding_sha256,context.bind_request_id,42000,now_us=42000)
assert parser.admit_binding(binding,now_us=42000)
for n in range(1,6):
 input=SourceStamp(scope.nn_input_stream,n,n+100,n+100,50000+n*1000)
 output=SourceStamp(scope.nn_output_stream,n,n+100,n+100,50100+n*1000)
 proof=ParserEvidence(context,input,output,50200+n*1000,True,True)
 assert parser.observe(proof,now_us=proof.completed_us)
assert parser.nn_ready and parser.streak==5 and parser.deadline.expires_us==15042000
assert not hasattr(parser,'metric_ready') and not hasattr(parser,'drive_permitted')
for table in (TIMING_US,ROLE_STATES):
 try:table['invented']=1
 except TypeError:pass
 else:raise AssertionError('mutable policy constants')
print('synthetic policy-only capture30/parser5 and isolation PASS')
"""
        result = subprocess.run([sys.executable, "-S", "-c", code], capture_output=True, text=True,
                                timeout=10, env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("isolation PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
