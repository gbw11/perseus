"""d1의 공개 판정을 격리한다. typed claim은 실제 OS/lock/생성 증거가 아니다."""

import os
import subprocess
import sys
import unittest


class ResourceGuardIsolationTests(unittest.TestCase):
    def test_all_five_paths_need_no_explicit_io_clock_uuid_or_native_ipc(self):
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
from dataclasses import replace
from resource_guard_fixtures import *
from xenova_vehicle.runtime.resource_guard import *
from xenova_vehicle.runtime.resource_manifest import RecoveryEpisode
m=manifest();g=guard(m);original_load=load(m)
d=evaluate_guard(original_load,g,old_evidence(m,g));assert d.kind=='OLD_CLEAN_ELIGIBLE'
assert d.predecessor is m and d.episode is m.recovery_episode and d.load is original_load
copy=m.fields;copy['state']='CLEAN';assert m.fields['state']=='DIRTY'
maximum=manifest(maximum_fields());assert len(maximum.canonical_bytes)==4319
ng=guard(maximum,scope=replace(maximum.runtime,boot_id=identifier(999)))
r=evaluate_guard(load(maximum),ng,GuardEvidence(reset=RuntimeResetStatement(maximum.ref,ng,identifier(888),'f'*64)))
assert r.kind=='FRESH_RESET_ELIGIBLE' and r.episode is maximum.recovery_episode
p=manifest(pending_fields());pg=guard(p,same_owner=True);s=next(o for o in p.known_owners if o.role=='S')
live=GuardEvidence(owners=(OwnerObservation(p.ref,pg,s,'ALIVE'),),live=LiveIntentStatement(p.ref,pg,p.intents[0].intent_id))
assert evaluate_guard(load(p),pg,live).kind=='LIVE_INTENT_PENDING'
missing=load(kind='MISSING');genesis=RecoveryEpisode(identifier(11),0,(0,0,0))
install=GuardEvidence(install=FirstInstallStatement(missing.load_id,g,identifier(889),'e'*64,genesis))
assert evaluate_guard(missing,g,install).kind=='FIRST_INSTALL_ELIGIBLE'
assert evaluate_guard(original_load,g,GuardEvidence()).kind=='BLOCKED'
for value in (d,r):
 for name in ('authorized','created','cleaned','owner_exited','lock_held','native_verified','actual_ready'):
  assert not hasattr(value,name)
print('pure d1 five-path isolation PASS; lower claims are not actual OS proof')
"""
        result = subprocess.run([sys.executable, '-B', '-S', '-c', code], capture_output=True,
                                text=True, encoding='utf-8', timeout=15,
                                env={**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn('five-path isolation PASS', result.stdout)


if __name__ == '__main__':
    unittest.main()
