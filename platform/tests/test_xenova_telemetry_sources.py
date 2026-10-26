"""Resource/provenance boundaries use production code; Xen/Store/filesystem faults
are explicit external substitutes. Hardware persistence is a separate acceptance."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'platform/yocto/meta-xenova/recipes-core/xenova-platform/files'))
sys.path.insert(0,str(ROOT/'platform/first-install'))
sys.path.insert(0,str(ROOT/'platform/guests/common'))
import xenova_telemetry_support as support
import xenova_telemetry_evidence as evidence

class SourcesTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.archive=self.base/'archive'
        self.context={'vm_id':str(uuid.uuid4()),'host_boot_id':str(uuid.uuid4()),
            'execution_id':str(uuid.uuid4()),'domid':7,'xen_unique_id':'18446744073709551615'}
        self.receipt={'context':self.context,'phase':'applied','boot_id':str(uuid.uuid4()),
            'metadata':{'artifact_sha256':'a'*64,'build_id':'sha256:'+'b'*64}}
    def test_names_keep_utf8_and_reject_controls_or_overflow(self):
        self.assertEqual(support.label('주행 A "\\',192),'주행 A "\\')
        for value in ('a\n','한'*65):
            with self.assertRaises(ValueError):support.label(value,192)
        self.assertEqual(support.display_label('🚗'*63),'🚗'*63)
        for value in ('🚗'*64,'a'*64):
            with self.assertRaises(ValueError):support.display_label(value)
    def test_metadata_uses_execution_loader_record_not_replaced_disk(self):
        runs=self.base/'executions';runs.mkdir()
        loaded={'file':'zephyr.bin','manifest_sha256':'a'*64,'artifact_sha256':'b'*64,
            'build_id':'sha256:'+'c'*64,'display_name':'주행 A','hostname':'vm-a'}
        p=runs/(self.context['vm_id']+'.json');run={**self.context,'phase':'observed','loaded_artifact':loaded}
        p.write_text(json.dumps(run))
        with patch('xenova_vm.storage_check'),patch.object(support,'capture_supported',return_value=False):
            actual=support.metadata(self.context,runtime=self.base,data=self.base)
            self.assertEqual(actual['artifact_sha256'],'b'*64)
            # No asset file is opened in the later provider; new disk content
            # cannot silently replace the loader's recorded identity.
            (self.base/'zephyr.bin').write_bytes(b'new on-disk firmware')
            self.assertEqual(support.metadata(self.context,runtime=self.base,data=self.base),actual)
            changed={**self.context,'execution_id':str(uuid.uuid4())}
            with self.assertRaises(ValueError):support.metadata(changed,runtime=self.base,data=self.base)
            run['loaded_artifact']['artifact_sha256']='not-a-hash';p.write_text(json.dumps(run))
            with self.assertRaises(ValueError):support.metadata(self.context,runtime=self.base,data=self.base)
    def test_clock_reply_needs_this_execution_and_preserves_integer(self):
        r={'nonce':'123','execution_id':self.context['execution_id']}
        s={'unix_ms':1767225600001,'source_error_ms':20,'drift_ppm':500,'source':'dom0_adjtimex'}
        self.assertEqual(support.clock_response(self.context,r,s)['unix_ms'],'1767225600001')
        r['execution_id']=str(uuid.uuid4())
        with self.assertRaises(ValueError):support.clock_response(self.context,r,s)
    def test_host_clock_quality_gate_and_uncertainty_components(self):
        from types import SimpleNamespace
        def read_timex(ptr):
            tx=ptr._obj;tx.maxerror=1201;tx.tolerance=500*65536;tx.status=status
            return state
        for status,state in ((0,0),(0x40,0),(0x1000,0),(0,5)):
            with patch.object(support.os,'uname',return_value=SimpleNamespace(machine='aarch64')), \
                 patch.object(support.Path,'read_text',return_value='arch_sys_counter\n'), \
                 patch.object(support.C,'CDLL',return_value=SimpleNamespace(adjtimex=read_timex)), \
                 patch.object(support.time,'monotonic_ns',side_effect=[0,1000001]), \
                 patch.object(support.time,'time_ns',return_value=1767225600000000000):
                if status or state:
                    with self.assertRaises(ValueError):support.clock_sample()
                else:
                    sample=support.clock_sample()
                    self.assertEqual(sample['source_error_ms'],4) # ceil(1.201ms) + ceil(1.000001ms)
                    self.assertEqual(sample['drift_ppm'],500)
    def test_archive_binding_fatal_error_partial_and_restart_query(self):
        source=self.base/'console';source.write_bytes(b'old unknown fatal\n')
        cursor=evidence.capture(source,self.archive,self.receipt,storage_guard=lambda:None)
        lines=(f"XDIAG_BIND {self.context['execution_id']} {self.receipt['boot_id']}\n"
            'secret-password-must-not-be-archived\nXDIAG_FACT 9 123 -5\nXDIAG_FATAL 3\nXDIAG_FA').encode()
        with source.open('ab') as f:f.write(lines)
        cursor=evidence.capture(source,self.archive,self.receipt,cursor=cursor,storage_guard=lambda:None)
        rows=evidence.query(self.archive)
        events=[e for row in rows for e in row['record']['events']]
        self.assertEqual([e['category'] for e in events],['error_observed','fatal_observed'])
        self.assertEqual(events[0]['occurred_uptime_ms'],123);self.assertIsNone(events[1]['occurred_uptime_ms'])
        self.assertNotIn('secret-password',json.dumps(rows))
        with source.open('ab') as f:f.write(b'TAL 4\n')
        evidence.capture(source,self.archive,self.receipt,cursor=cursor,storage_guard=lambda:None)
        self.assertEqual(sum(len(r['record']['events']) for r in evidence.query(self.archive)),3)
        # Simulated collector restart reads durable files, not a RAM cache.
        self.assertTrue(all(row['status']=='stored' for row in evidence.query(self.archive)))
    def test_archive_store_failure_never_claims_durable_or_discards_source(self):
        source=self.base/'console';source.write_bytes(b'')
        with patch.object(evidence,'atomic_write',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):evidence.capture(source,self.archive,self.receipt,storage_guard=lambda:None)
        self.assertEqual(source.read_bytes(),b'')
        self.archive.mkdir(mode=0o700,exist_ok=True);(self.archive/'partial.json').write_bytes(b'{')
        self.assertEqual(evidence.query(self.archive)[0]['status'],'incomplete')
    def test_async_creation_and_delayed_old_binding_never_relabel_new_execution(self):
        runs=self.base/'executions';runs.mkdir();sources=self.base/'scratch';sources.mkdir()
        (runs/(self.context['vm_id']+'.json')).write_text(json.dumps(self.context))
        support.register_capture(self.context,self.base/'receipt',runtime=self.base,sources=sources)
        registration=json.loads((self.base/'telemetry-capture'/(self.context['vm_id']+'.json')).read_text())
        self.assertTrue(registration['cursor']['pending_source'])
        source=Path(registration['source']);self.assertFalse(source.exists())
        old_execution=str(uuid.uuid4());old_boot=str(uuid.uuid4())
        current=f"XDIAG_BIND {self.context['execution_id']} {self.receipt['boot_id']}\n"
        old=f"XDIAG_BIND {old_execution} {old_boot}\n"
        source.write_text('XDIAG_STREAM 100\n'+old+'XDIAG_FACT 1 1 -1\n'+current+
                          'XDIAG_FACT 2 2 -2\n'+old+'XDIAG_FACT 3 3 -3\n'+current+'XDIAG_FACT 4 4 -4\n')
        cursor=evidence.capture(source,self.archive,self.receipt,cursor=registration['cursor'],storage_guard=lambda:None)
        records=[r['record'] for r in evidence.query(self.archive)]
        self.assertEqual([e['event_seq'] for r in records for e in r['events']],[2,4])
        self.assertTrue(all(r['context']==self.context for r in records));self.assertTrue(cursor['bound'])
    def test_retention_removes_old_committed_only_and_records_reason(self):
        self.archive.mkdir(mode=0o700)
        def record(name):
            p=self.archive/name;p.write_text(json.dumps({'schema_version':'1.0-evidence',
                'context':self.context,'events':[],'end_offset':1,'capture_complete':True}))
            p.chmod(0o600);return p
        oldest=record('old.json');newest=record('new.json')
        os.utime(oldest,ns=(1,1));os.utime(newest,ns=(2,2))
        partial=self.archive/'partial.json';partial.write_bytes(b'{')
        with evidence.retention_lock(self.archive):
            evidence._prune(self.archive,self.archive,0,max_records=2,per_execution=2)
        self.assertFalse(oldest.exists());self.assertTrue(newest.exists());self.assertTrue(partial.exists())
        journal=json.loads((self.archive/'retention.json').read_text())
        self.assertEqual(journal['removed_total'],1)
        self.assertEqual(journal['recent'][0]['reason'],'retention_limit')
        self.assertEqual(journal['recent'][0]['outcome'],'removed')
    def test_retention_plan_must_be_durable_before_deletion(self):
        self.archive.mkdir(mode=0o700)
        old=self.archive/'old.json';old.write_text(json.dumps({'schema_version':'1.0-evidence',
            'context':self.context,'events':[],'end_offset':1}));old.chmod(0o600)
        with evidence.retention_lock(self.archive),patch.object(evidence,'atomic_write',side_effect=OSError('fsync failed')):
            with self.assertRaises(OSError):evidence._prune(self.archive,self.archive,1,max_records=1,per_execution=1)
        self.assertTrue(old.exists())
    def test_retention_resumes_planned_removal_and_enforces_byte_capacity(self):
        self.archive.mkdir(mode=0o700)
        old=self.archive/'old.json'
        old.write_text(json.dumps({'schema_version':'1.0-evidence','context':self.context,
            'events':[],'end_offset':1}));old.chmod(0o600)
        original=evidence.atomic_write
        writes=0
        def interrupted(path,*args):
            nonlocal writes
            writes+=1
            if writes==2:raise OSError('interrupted after unlink')
            return original(path,*args)
        size=old.stat().st_size
        with evidence.retention_lock(self.archive),patch.object(evidence,'atomic_write',side_effect=interrupted):
            with self.assertRaises(OSError):
                evidence._prune(self.archive,self.archive,size,max_bytes=size,max_records=9)
        self.assertFalse(old.exists())
        with evidence.retention_lock(self.archive):
            evidence._prune(self.archive,self.archive,size,max_bytes=size,max_records=9)
        journal=json.loads((self.archive/'retention.json').read_text())
        self.assertFalse(journal['pending'])
        self.assertEqual(journal['recent'][0]['outcome'],'absent_after_plan')
        self.assertEqual(journal['removed_total'],0) # A lost confirmation is not invented.
    def test_oversized_record_cannot_delete_history(self):
        self.archive.mkdir(mode=0o700)
        old=self.archive/'old.json'
        old.write_text(json.dumps({'schema_version':'1.0-evidence','context':self.context,
            'events':[],'end_offset':1}));old.chmod(0o600)
        with evidence.retention_lock(self.archive):
            with self.assertRaises(OSError) as error:
                evidence._prune(self.archive,self.archive,1025,max_bytes=1024)
        self.assertEqual(error.exception.errno,__import__('errno').EFBIG)
        self.assertTrue(old.exists())
    def test_existing_console_owner_filter_fragmentation_and_secret_exclusion(self):
        header=ROOT/'platform/yocto/meta-xenova/recipes-extended/xen/files'
        c=self.base/'filter.c';binary=self.base/'filter'
        c.write_text('''#include "xenova_console_evidence.h"
#include <assert.h>
static int output(void *ctx,const char *s,size_t n){(void)ctx;return fwrite(s,1,n,stdout)==n?0:-1;}
int main(void){struct xdlog f={0};char input[1024];size_t n;
while((n=fread(input,1,7,stdin))){assert(!xdlog_feed(&f,input,n,output,NULL));}
return 0;}
''')
        result=subprocess.run([shutil.which('cc'),'-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-no-pie','-I'+str(header),str(c),'-o',str(binary)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        raw=(f"password secret\nXDIAG_BIND {self.context['execution_id']} {self.receipt['boot_id']}\r\n"
            'XDIAG_EVENT {"seq":2,"uptime_ms":123,"code":-5,"stage_hex":"secret"}\n'
            '>>> ZEPHYR FATAL ERROR 3: secret detail\n'+'x'*800+'\n').encode()
        got=subprocess.run([str(binary)],input=raw,capture_output=True,check=True).stdout
        self.assertIn(b'XDIAG_BIND ',got);self.assertIn(b'XDIAG_FACT 2 123 -5',got);self.assertIn(b'XDIAG_FATAL 3',got)
        self.assertNotIn(b'secret',got);self.assertNotIn(b'xxx',got)
    def test_filtered_file_rotation_rebinds_and_never_invents_continuity(self):
        header=ROOT/'platform/yocto/meta-xenova/recipes-extended/xen/files'
        c=self.base/'rotate.c';binary=self.base/'rotate';source=self.base/'filtered.log'
        c.write_text('''#define _POSIX_C_SOURCE 200809L
#include "xenova_console_evidence_file.h"
#include <assert.h>
#include <fcntl.h>
#include <errno.h>
static int output(int fd,const char *s,size_t n){while(n){ssize_t got=write(fd,s,n);
 if(got<0&&errno==EINTR)continue;
 if(got<=0)return -1;
 s+=got;n-=(size_t)got;}return 0;}
int main(int argc,char **argv){assert(argc==2);struct xdlog filter={0};
 int fd=open(argv[1],O_WRONLY|O_CREAT|O_APPEND,0600);assert(fd>=0);
 struct xdlog_file file={.fd=fd,.filter=&filter,.write_all=output};
 char input[73];size_t n;while((n=fread(input,1,sizeof(input),stdin)))
 {assert(!xdlog_feed(&filter,input,n,xdlog_file_write,&file));}
 assert(!close(fd));return 0;}
''')
        built=subprocess.run([shutil.which('cc'),'-std=c11','-Wall','-Wextra','-Werror',
            '-fsanitize=address,undefined','-no-pie','-I'+str(header),str(c),'-o',str(binary)],capture_output=True,text=True)
        self.assertEqual(built.returncode,0,built.stderr)
        binding=f"XDIAG_BIND {self.context['execution_id']} {self.receipt['boot_id']}\n"
        event=lambda i:'XDIAG_EVENT '+json.dumps({'seq':i,'uptime_ms':i*10,'code':-5,
            'stage_hex':'00','boot':False},separators=(',',':'))+'\n'
        subprocess.run([str(binary),str(source)],input=(binding+event(1)).encode(),check=True,capture_output=True)
        cursor=evidence.capture(source,self.archive,self.receipt,storage_guard=lambda:None)
        old_generation=cursor['stream_generation'];old_inode=source.stat().st_ino
        # Same inode, already regrown beyond the prior offset: size alone cannot
        # detect rotation. The real writer must leave a new generation + bind.
        payload=binding+''.join(event(i) for i in range(2,30000))
        subprocess.run([str(binary),str(source)],input=payload.encode(),check=True,capture_output=True)
        self.assertEqual(source.stat().st_ino,old_inode);self.assertLessEqual(source.stat().st_size,256*1024)
        position=evidence.capture(source,self.archive,self.receipt,cursor=cursor,storage_guard=lambda:None)
        self.assertNotEqual(position['stream_generation'],old_generation)
        rows=[r['record'] for r in evidence.query(self.archive) if r['status']=='stored']
        rotated=next(r for r in rows if r['continuity']=='rotation_gap')
        self.assertTrue(rotated['events']);self.assertEqual(rotated['source_loss'],'not_quantified')
