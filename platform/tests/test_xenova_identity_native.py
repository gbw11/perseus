"""CLI/ABI parity with the existing Python helper, using a local fake library."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

FILES = Path(__file__).resolve().parents[1] / 'yocto/meta-xenova/recipes-core/xenova-platform/files'
FAKE = r'''
#define _POSIX_C_SOURCE 200809L
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <time.h>
static int mode(const char *s) { const char *m=getenv("IDENTITY_TEST_MODE"); return m && !strcmp(m,s); }
static void trace(const char *s) {
 const char *p=getenv("IDENTITY_TEST_TRACE_PATH");
 if(p) { FILE *f=fopen(p,"a"); if(f) {fprintf(f,"%s\n",s); fclose(f);} }
}
void *xenmanage_open(void *unused, unsigned flags) {
 (void)unused;(void)flags;trace("open");
 if(mode("open_error")){errno=EACCES;return NULL;} return (void *)1;
}
int xenmanage_close(void *h){(void)h;trace("close");return 0;}
int xenmanage_get_domain_info(void *h, unsigned domid, unsigned *state, unsigned *caps, uint64_t *unique){
 (void)h;(void)domid;trace("get");
 if(mode("get_error")){errno=ENOENT;return -1;}
 if(mode("blocked")){struct timespec t={2,0};nanosleep(&t,NULL);}
 *state=mode("absent")?0:1;*caps=0;*unique=mode("zero_unique")?0:UINT64_MAX;return 0;
}
'''


@pytest.fixture(scope='module')
def compiled(tmp_path_factory):
    compiler = shutil.which('gcc')
    if not compiler:
        pytest.skip('host C compiler required')
    root = tmp_path_factory.mktemp('identity-abi')
    source = root / 'fake.c'
    source.write_text(FAKE)
    subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-fPIC', '-shared',
                    str(source), '-o', str(root/'libxenmanage.so.1')], check=True, capture_output=True)
    binary = root/'identity-native'
    subprocess.run([compiler, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                    str(FILES/'xenova_domain_identity_native.c'), '-o', str(binary), '-ldl'],
                   check=True, capture_output=True)
    return root, binary


@pytest.mark.parametrize('arguments', [['--domid','1'], ['--domid','32751'],
    ['--domid','0','--allow-dom0'], ['--allow-dom0','--domid=0']])
def test_native_and_python_return_identical_64_bit_strings(compiled, arguments):
    root, binary = compiled
    env = dict(os.environ, LD_LIBRARY_PATH=str(root))
    native = subprocess.run([str(binary), *arguments], env=env, capture_output=True, text=True, check=True)
    python = subprocess.run([sys.executable, str(FILES/'xenova_domain_identity.py'), *arguments],
                            env=env, capture_output=True, text=True, check=True)
    assert json.loads(native.stdout) == json.loads(python.stdout)
    assert json.loads(native.stdout)['xen_unique_id'] == str(2**64-1)


@pytest.mark.parametrize('mode', ['open_error','get_error','absent','zero_unique'])
def test_error_has_no_success_payload_and_always_closes_an_open_handle(compiled, tmp_path, mode):
    root, binary = compiled
    for name, command in [('native',[str(binary)]), ('python',[sys.executable,str(FILES/'xenova_domain_identity.py')])]:
        path=tmp_path/name
        env=dict(os.environ, LD_LIBRARY_PATH=str(root), IDENTITY_TEST_MODE=mode, IDENTITY_TEST_TRACE_PATH=str(path))
        result=subprocess.run([*command,'--domid','2'],env=env,capture_output=True,text=True)
        assert result.returncode == 1 and not result.stdout
        assert path.read_text().splitlines() == (['open'] if mode=='open_error' else ['open','get','close'])


@pytest.mark.parametrize('domid', ['0','-1','32752','999999999999999999999999','invalid'])
def test_invalid_guest_domid_does_not_open_library_handle(compiled, tmp_path, domid):
    root, binary = compiled
    trace=tmp_path/'trace'
    result=subprocess.run([str(binary),'--domid',domid],env=dict(os.environ,LD_LIBRARY_PATH=str(root),
        IDENTITY_TEST_TRACE_PATH=str(trace)),capture_output=True,text=True)
    assert result.returncode != 0 and not result.stdout and not trace.exists()


def test_caller_deadline_can_still_terminate_blocked_native_process(compiled):
    root, binary = compiled
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run([str(binary),'--domid','2'],env=dict(os.environ,LD_LIBRARY_PATH=str(root),
            IDENTITY_TEST_MODE='blocked'),capture_output=True,timeout=.15)
