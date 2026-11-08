"""Read-only batching retains the ABI, all-or-nothing output and caller deadline."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import test_xenova_identity_native as single

FILES = single.FILES


@pytest.fixture(scope='module')
def compiled(tmp_path_factory):
    compiler = shutil.which('gcc')
    if not compiler: pytest.skip('host compiler required')
    root = tmp_path_factory.mktemp('batch-identity')
    fake = single.FAKE.replace('if(mode("get_error"))', 'if(mode("get_error") && domid==2)')
    (root/'fake.c').write_text(fake)
    subprocess.run([compiler,'-std=c11','-Wall','-Wextra','-Werror','-fPIC','-shared',
                    str(root/'fake.c'),'-o',str(root/'libxenmanage.so.1')],check=True,capture_output=True)
    binary = root/'batch'
    subprocess.run([compiler,'-std=c11','-O2','-Wall','-Wextra','-Werror',
                    str(FILES/'xenova_domain_identities_native.c'),'-o',str(binary),'-ldl'],check=True,capture_output=True)
    return root,binary


def run(compiled, arguments, **extra):
    root,binary=compiled
    return subprocess.run([str(binary),*arguments],env=dict(os.environ,LD_LIBRARY_PATH=str(root),**extra),capture_output=True,text=True)


def test_many_guests_share_one_handle_and_preserve_full_uint64(compiled,tmp_path):
    trace=tmp_path/'trace'
    r=run(compiled,['--domids','1,2,32751'],IDENTITY_TEST_TRACE_PATH=str(trace))
    assert r.returncode==0
    assert json.loads(r.stdout)=={'schema_version':1,'items':[
        {'domid':d,'xen_unique_id':str(2**64-1),'state_bits':1} for d in (1,2,32751)]}
    assert trace.read_text().splitlines()==['open','get','get','get','close']


def test_second_guest_failure_never_writes_partial_success(compiled,tmp_path):
    trace=tmp_path/'trace'
    r=run(compiled,['--domids','1,2,3'],IDENTITY_TEST_MODE='get_error',IDENTITY_TEST_TRACE_PATH=str(trace))
    assert r.returncode!=0 and r.stdout==''
    assert trace.read_text().splitlines()==['open','get','get','close']


@pytest.mark.parametrize('text',['','0','-1','01','1,','1,,2','1,1','1, 2','1,+2','32752','9'*30,','.join(map(str,range(1,258)))])
def test_bad_input_does_not_open_xen_handle(compiled,tmp_path,text):
    trace=tmp_path/'trace'
    r=run(compiled,['--domids',text],IDENTITY_TEST_TRACE_PATH=str(trace))
    assert r.returncode!=0 and not r.stdout and not trace.exists()


@pytest.mark.parametrize('mode',['open_error','absent','zero_unique'])
def test_no_invalid_identity_success(compiled,mode):
    r=run(compiled,['--domids','1,2'],IDENTITY_TEST_MODE=mode)
    assert r.returncode!=0 and not r.stdout


def test_parent_can_terminate_a_blocked_batch(compiled):
    root,binary=compiled
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run([str(binary),'--domids','1,2'],env=dict(os.environ,LD_LIBRARY_PATH=str(root),
                       IDENTITY_TEST_MODE='blocked'),capture_output=True,timeout=.15)


def test_bounded_maximum_batch_is_supported(compiled):
    r=run(compiled,['--domids',','.join(map(str,range(1,257)))])
    assert r.returncode==0 and len(json.loads(r.stdout)['items'])==256
