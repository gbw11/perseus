"""구조/자료구조의 격리 실행. 합성 opaque packet은 peer/typed/admission 성공이 아니다."""

import os
import subprocess
import sys
import unittest


class EnvelopeIsolationTests(unittest.TestCase):
    def test_unsupported_python_value_cannot_run_metaclass_comparison_hooks(self):
        from envelope_fixtures import fields
        from xenova_vehicle.runtime.local_envelope import encode_structure, EnvelopeStructureError
        calls = []

        class Meta(type):
            def __eq__(cls, other):
                calls.append(other)
                raise AssertionError("unsupported type equality hook executed")

        class Unsupported(metaclass=Meta):
            pass

        with self.assertRaises(EnvelopeStructureError):
            encode_structure(fields(payload={'unsupported': Unsupported()}))
        self.assertEqual(calls, [])

    def test_structural_samples_and_queues_need_no_io_native_clock_or_process(self):
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
from xenova_vehicle.runtime.local_envelope import encode_structure,decode_structure,StructuralPacket
from xenova_vehicle.runtime.mailbox import RxMailbox,PendingTx
from envelope_fixtures import fields
f=fields(payload={'opaque':{'unknown_typed_field':[1.5,True,None,'한글']}})
f['issued_j_us']='999'
f['expires_j_us']='1'
p=encode_structure(f)
assert decode_structure(p.raw_bytes).fields==f
assert not hasattr(p,'authorized') and not hasattr(p,'typed_payload_valid')
original_bytes=p.raw_bytes
f['payload']['opaque']['unknown_typed_field'].clear()
external=p.fields;external['payload'].clear()
assert p.raw_bytes==original_bytes and p.payload['opaque']['unknown_typed_field']
rx=RxMailbox();tx=PendingTx()
assert rx.put(p).accepted and tx.accept(p)
handle=tx.peek()
assert tx.peek() is handle and handle.raw_bytes==original_bytes
terminal=encode_structure(fields('FAULT',payload={'opaque':'not a deployed fault schema'}))
assert rx.put(terminal).accepted and not tx.accept(terminal)
assert rx.pop() is terminal and tx.peek() is handle
assert tx.finish(handle) and tx.accept(p)
fresh=tx.peek()
assert fresh is not handle and fresh.raw_bytes==original_bytes
assert not tx.finish(handle) and tx.peek() is fresh
assert tx.finish(fresh) and not tx.finish(fresh)
assert rx.pop() is p and rx.pop() is None
assert rx.held_bytes==tx.held_bytes==0
try:StructuralPacket(b'{}')
except ValueError:pass
else:raise AssertionError('unchecked public constructor')
print('structural-only isolation and synthetic RX/TX PASS')
"""
        result = subprocess.run([sys.executable, "-B", "-S", "-c", code], capture_output=True,
                                text=True, encoding="utf-8", timeout=10,
                                env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("synthetic RX/TX PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
