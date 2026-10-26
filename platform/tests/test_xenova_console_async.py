"""Optional scratch copies cannot backpressure the existing console owner.

The queue is real pthread/eventfd code. With the pinned Xen source supplied,
the PTY test compiles its patched buffer_append/handle_tty_write functions;
only the external PV ring/event-channel boundary is substituted.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
FILES=ROOT/'platform/yocto/meta-xenova/recipes-extended/xen/files'

def function(text, declaration):
    start=text.index(declaration);brace=text.index('{',start);depth=1;end=brace+1
    while depth:
        depth+=(text[end]=='{')-(text[end]=='}');end+=1
    return text[start:end]

class ConsoleAsyncTest(unittest.TestCase):
    def test_nonwaiting_queue_failure_exit_and_original_binding(self):
        self.run_fixture('')

    @unittest.skipUnless(os.environ.get('XENOVA_XEN_CONSOLE_SOURCE'), 'pinned Xen source not configured')
    def test_actual_xen_ring_to_two_ptys_while_writer_stalls(self):
        source=Path(os.environ['XENOVA_XEN_CONSOLE_SOURCE'])
        with tempfile.TemporaryDirectory() as directory:
            tree=Path(directory);target=tree/'tools/console/daemon';target.mkdir(parents=True)
            for name in ('io.c','Makefile'):shutil.copyfile(source/name,target/name)
            applied=subprocess.run(['patch','-p1','--batch','-i',str(FILES/'0002-console-filter-xenova-evidence.patch')],cwd=tree,capture_output=True,text=True)
            self.assertEqual(applied.returncode,0,applied.stdout+applied.stderr)
            text=(target/'io.c').read_text()
            # Actual parent open path must return before any optional Store/file I/O.
            opener=function(text,'static int create_console_log(')
            self.assertLess(opener.index('return -1;'),opener.index('xs_get_domain_path'))
            methods='\n'.join(function(text,s) for s in ('static void buffer_advance(', 'static void buffer_append(', 'static void handle_tty_write('))
            self.run_fixture(methods)

    def run_fixture(self, methods):
        with tempfile.TemporaryDirectory() as directory:
            c=Path(directory)/'test.c';binary=Path(directory)/'test'
            body=BODY.replace('/* FUNCTION_BINDINGS */','append_fn=buffer_append;tty_fn=handle_tty_write;' if methods else '')
            c.write_text(PREFIX+methods+body)
            built=subprocess.run(['cc','-std=c11','-O2','-D_FORTIFY_SOURCE=2','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable',
                '-fsanitize=address,undefined','-no-pie','-pthread','-I'+str(FILES),str(c),'-lutil','-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(built.returncode,0,built.stderr)
            for case in ('stall','lock','failure','exit','unavailable'):
                result=subprocess.run([str(binary),case,'pty' if methods else 'queue'],capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertIn('PASS',result.stdout)
                if os.environ.get('XENOVA_TEST_LOG_DIR'):
                    out=Path(os.environ['XENOVA_TEST_LOG_DIR']);out.mkdir(exist_ok=True,parents=True)
                    (out/f'console-{case}-{"pty" if methods else "queue"}.log').write_text(result.stdout)

PREFIX=r'''#define _GNU_SOURCE
#include "xenova_console_evidence_async.h"
#include <assert.h>
#include <stdatomic.h>
#include <termios.h>
#include <pty.h>
#include <stdarg.h>
typedef unsigned XENCONS_RING_IDX;
#define xen_mb() ((void)0)
#define MASK_XENCONS_IDX(i,r) ((i) % sizeof(r))
#define LOG_ERR 3
#define LOG_DEBUG 7
struct buffer {char *data;size_t size,consumed,capacity,max_capacity;};
struct domain {unsigned domid;bool is_dead;};
struct xencons_interface {unsigned out_cons,out_prod;char out[1024];};
struct console {int log_fd,master_fd,local_port;void *xce_handle;const char *log_suffix;
 struct buffer buffer;struct domain *d;struct xencons_interface *interface;struct xdlog evidence;};
static char *log_dir="/not-an-existing-evidence-directory";
static bool discard_overflowed_data,log_time_guest,log_time_guest_needts;
static int write_all(int fd,const char *b,size_t n){return xdlog_write_all(fd,b,n);}
static int write_with_timestamp(int fd,const char *b,size_t n,bool *ts){(void)ts;return write_all(fd,b,n);}
static void dolog(int level,const char *fmt,...){(void)level;(void)fmt;}
static void xenevtchn_notify(void *h,int port){(void)h;(void)port;}
static bool domain_is_valid(unsigned id){(void)id;return true;}
static void console_handle_broken_tty(struct console *c,bool valid){(void)c;(void)valid;assert(0);}
'''
BODY=r'''
static pthread_mutex_t gate=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t changed=PTHREAD_COND_INITIALIZER;
static bool entered,released;static const char *mode;
static atomic_uint stored;
static char first_binding[86];
static void sink(const struct xdlog_job *job){
 if(!strcmp(mode,"lock"))pthread_mutex_lock(&xdlog_async.lock);
 pthread_mutex_lock(&gate);entered=true;pthread_cond_broadcast(&changed);
 while(!released)pthread_cond_wait(&changed,&gate);
 pthread_mutex_unlock(&gate);
 if(!strcmp(mode,"lock"))pthread_mutex_unlock(&xdlog_async.lock);
 if(!strcmp(mode,"exit"))pthread_exit(NULL);
 if(!strcmp(mode,"failure"))return;
 if(atomic_load(&stored)==0)memcpy(first_binding,job->binding,86);
 atomic_fetch_add(&stored,1);
}
static void (*append_fn)(struct console *);
static void (*tty_fn)(struct console *);
static void feed(struct console *c,const char *raw){
 size_t n=strlen(raw);assert(n<=1024);
 if(append_fn){unsigned base=c->interface->out_prod;
  for(size_t i=0;i<n;i++)c->interface->out[(base+i)%1024]=raw[i];
  c->interface->out_prod+=(unsigned)n;append_fn(c);tty_fn(c);
 }else{struct xdlog_enqueue e={.filter=&c->evidence,.directory=log_dir,.suffix="",.domid=c->d->domid};
  (void)xdlog_feed(&c->evidence,raw,n,xdlog_enqueue,&e);}
}
int main(int argc,char **argv){
 assert(argc==3);mode=argv[1];setenv("XENOVA_CONSOLE_EVIDENCE","1",1);
 /* FUNCTION_BINDINGS */
 if(!strcmp(mode,"unavailable")){xdlog_async.ready=false;}
 else assert(!xdlog_async_start(sink));
 struct domain domains[2]={{.domid=1},{.domid=2}};
 struct xencons_interface rings[2]={0};struct console consoles[2]={0};int slaves[2]={-1,-1};
 for(unsigned i=0;i<2;i++){
  consoles[i].d=&domains[i];consoles[i].interface=&rings[i];consoles[i].log_fd=-1;consoles[i].log_suffix="";
  if(append_fn){assert(!openpty(&consoles[i].master_fd,&slaves[i],NULL,NULL,NULL));
   struct termios t;assert(!tcgetattr(slaves[i],&t));cfmakeraw(&t);assert(!tcsetattr(slaves[i],TCSANOW,&t));}
 }
 const char *unbound="XDIAG_FACT 1 1 -5\n>>> ZEPHYR FATAL ERROR 3: detail\n";
 feed(&consoles[0],unbound);assert(!xdlog_async.count);
 if(append_fn){char got[256];assert(read(slaves[0],got,strlen(unbound))==(ssize_t)strlen(unbound));assert(!memcmp(got,unbound,strlen(unbound)));}
 const char *old="XDIAG_BIND 11111111-1111-4111-8111-111111111111 22222222-2222-4222-8222-222222222222\n";
 const char *newer="XDIAG_BIND 33333333-3333-4333-8333-333333333333 44444444-4444-4444-8444-444444444444\n";
 feed(&consoles[0],old);
 if(append_fn){char got[256];assert(read(slaves[0],got,strlen(old))==(ssize_t)strlen(old));assert(!memcmp(got,old,strlen(old)));}
 if(strcmp(mode,"unavailable")){
  pthread_mutex_lock(&gate);while(!entered)pthread_cond_wait(&changed,&gate);pthread_mutex_unlock(&gate);
 }
 /* The lock scenario makes the worker hold its queue mutex through the fault
  * gate. Returning here before release proves the producer never waits on it. */
 feed(&consoles[1],"ordinary PTY bytes 한글\n");
 if(append_fn){char got[128];const char *s="ordinary PTY bytes 한글\n";assert(read(slaves[1],got,strlen(s))==(ssize_t)strlen(s));assert(!memcmp(got,s,strlen(s)));}
 for(unsigned n=0;n<250;n++)for(unsigned i=0;i<2;i++){
  const char *raw=n==1?newer:"XDIAG_EVENT {\"seq\":7,\"uptime_ms\":8,\"code\":-5,\nunchanged raw PTY\n";
  feed(&consoles[i],raw);
  if(append_fn){char got[1024];size_t count=strlen(raw);assert(read(slaves[i],got,count)==(ssize_t)count);assert(!memcmp(got,raw,count));}
 }
 assert(consoles[0].evidence.dropped>0&&consoles[1].evidence.dropped>0);
 assert(!atomic_load(&stored)); /* Queued != durable; worker still blocked. */
 pthread_mutex_lock(&gate);released=true;pthread_cond_broadcast(&changed);pthread_mutex_unlock(&gate);
 if(!strcmp(mode,"stall")||!strcmp(mode,"lock")){
  for(unsigned i=0;!atomic_load(&stored)&&i<1000;i++)usleep(1000);
  assert(atomic_load(&stored));assert(!memcmp(first_binding,old,85));
 }else{usleep(10000);assert(!atomic_load(&stored));}
 /* Continue forwarding after the worker has exited/failed, not only while it
  * is stalled. No implicit restart, synchronous fallback or queue drain. */
 for(unsigned i=0;i<2;i++){
  const char *raw="XDIAG_EVENT {\"seq\":8,\"uptime_ms\":9,\"code\":-5,\n";feed(&consoles[i],raw);
  if(append_fn){char got[256];assert(read(slaves[i],got,strlen(raw))==(ssize_t)strlen(raw));assert(!memcmp(got,raw,strlen(raw)));}
 }
 /* No drain/join/recovery waits at exit, even with failed/dead worker + queue. */
 printf("PASS %s %s dropped=%llu/%llu stored=%u\n",mode,argv[2],
  (unsigned long long)consoles[0].evidence.dropped,(unsigned long long)consoles[1].evidence.dropped,atomic_load(&stored));
 for(unsigned i=0;i<2;i++){free(consoles[i].buffer.data);if(append_fn){close(consoles[i].master_fd);close(slaves[i]);}}
 return 0;
}
'''
