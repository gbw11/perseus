"""Self-contained local IPC demonstration. Only owned temporary lab assets.

Separate Gateway process, real socket/Authority, simulated apps/output. Does not
start a broker, open a serial device, or execute VM recovery.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from uuid import uuid4
from xenova_gateway.client import GatewayClient,GatewayRejected,GatewayUnavailable


def main():
    with tempfile.TemporaryDirectory(prefix='xenova-gateway-lab-') as directory:
        proc=subprocess.Popen([sys.executable,'-B',str(Path(__file__).with_name('provider_lab.py')),
            '--directory',directory],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            line=proc.stdout.readline()
            if not line:raise RuntimeError(proc.stderr.read())
            ready=json.loads(line);client=GatewayClient(ready['endpoint'],uid=ready['uid'])
            context=client.context();target='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
            binding=context['targets'][target];request_id=str(uuid4()) # caller persists before first submission
            spec=dict(host_boot_id=context['host_boot_id'],target_vm=target,
                target_execution_id=binding['execution_id'],intent_revision=binding['intent_revision'],
                lifecycle_generation=binding['lifecycle_generation'],expected_owner=context['owner'],
                expected_generation=context['generation'],deadline_ms=int(time.monotonic()*1000)+2000)
            try:result=client.submit(request_id,spec)
            except GatewayUnavailable:
                # Communication failure does not mean cancellation/nonacceptance.
                result=client.get(request_id) # same key, no fresh deadline/approval
            assert result['phase']=='PROTECTED' and result['protection']['held']
            assert result['protection']['current_target_matches'] and result['protection']['current_host_matches']
            equivalent={**spec,'purpose':'protect','parent_deadline_ms':spec['deadline_ms']}
            assert client.submit(request_id,equivalent)['phase']=='PROTECTED'
            try:client.submit(request_id,{**spec,'intent_revision':spec['intent_revision']+1})
            except GatewayRejected as error:assert error.code=='IDEMPOTENCY_CONFLICT'
            else:raise AssertionError('different intent accepted')
            current=client.context()
            cancelled=client.cancel(request_id,context=dict(host_boot_id=current['host_boot_id'],
                expected_generation=current['generation'],target_execution_id=binding['execution_id']))
            assert cancelled['phase']=='CANCELLED' and not cancelled['protection']['held']
            print(json.dumps(dict(fixture=True,request_id=request_id,submitted=result,
                                  recovered=client.get(request_id),cancelled=cancelled)))
        finally:
            proc.terminate();proc.wait(timeout=5)
            stdout,stderr=proc.communicate()
            if proc.returncode!=0:raise RuntimeError(stderr)
            print(stdout.strip())


if __name__=='__main__':main()
