"""The production Store wrapper retains timeout cause and locked session health."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]


class StoreObservationStatusTest(unittest.TestCase):
    def test_original_watch_and_checked_watch_share_one_bounded_session(self):
        with tempfile.TemporaryDirectory(prefix='netfront-store-status-') as tmp:
            home=Path(tmp)
            headers={
                'zephyr/kernel.h':'''#pragma once
#include <stdbool.h>
#include <stdint.h>
struct k_mutex { int unused; };
#define K_MUTEX_DEFINE(n) struct k_mutex n
#define K_MSEC(v) (v)
#define MAX(a,b) ((a)>(b)?(a):(b))
bool k_is_in_isr(void);
int64_t k_uptime_get(void);
int k_mutex_lock(struct k_mutex *,int64_t);
void k_mutex_unlock(struct k_mutex *);
''',
                'xenova/xenstore_zephyr.h':'''#pragma once
#include <xenova/xenstore.h>
struct xs_zephyr { struct { struct xs_client client; bool open; } link; };
int xs_zephyr_open(struct xs_zephyr *);
int xs_zephyr_close(struct xs_zephyr *,int);
'''}
            for name,text in headers.items():
                p=home/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
            source=home/'main.c';source.write_text(r'''
#include <xenova/runtime.h>
#include <xenova/xenstore_zephyr.h>
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
static int locked,deny,mode,reads,opens;
bool k_is_in_isr(void){return false;}
int64_t k_uptime_get(void){return 100;}
int k_mutex_lock(struct k_mutex *m,int64_t ms){(void)m;(void)ms;if(deny)return -EAGAIN;assert(!locked);locked=1;return 0;}
void k_mutex_unlock(struct k_mutex *m){(void)m;assert(locked);locked=0;}
int xs_zephyr_open(struct xs_zephyr *s){memset(s,0,sizeof(*s));s->link.open=true;opens++;return 0;}
int xs_zephyr_close(struct xs_zephyr *s,int ms){(void)ms;s->link.open=false;return 0;}
int xs_watch_next(struct xs_client *c,uint32_t id,struct xs_watch_event *event,int ms){
 (void)id;(void)event;assert(locked&&ms<=50);reads++;
 if(mode==1){c->poisoned=true;c->fatal_error=-ETIMEDOUT;}
 if(mode==2)c->closed=true;
 return -ETIMEDOUT;
}
int xs_unwatch(struct xs_client *c,uint32_t id,int ms){(void)c;(void)id;(void)ms;return 0;}
int xs_read_text(struct xs_client *c,uint32_t tx,const char *p,char *v,size_t n,int ms){(void)c;(void)tx;(void)p;(void)v;(void)n;(void)ms;return 0;}
int xs_write(struct xs_client *c,uint32_t tx,const char *p,const void *v,size_t n,int ms){(void)c;(void)tx;(void)p;(void)v;(void)n;(void)ms;return 0;}
int xs_transaction(struct xs_client *c,unsigned limit,int ms,xs_transaction_fn body,void *ctx,unsigned *used){(void)c;(void)limit;(void)ms;(void)body;(void)ctx;(void)used;return 0;}
int main(void){
 assert(!xsr_open()&&opens==1);bool unusable=true;
 assert(xsr_watch_next_status(1,50,&unusable)==-ETIMEDOUT&&!unusable&&!locked);
 assert(xsr_watch_next(1,50)==-ETIMEDOUT&&!locked);
 deny=1;unusable=true;int before=reads;
 assert(xsr_watch_next_status(1,50,&unusable)==-ETIMEDOUT&&!unusable&&reads==before&&!locked);deny=0;
 mode=1;assert(xsr_watch_next_status(1,50,&unusable)==-ETIMEDOUT&&unusable&&!locked);
 struct xs_client *c=xsr_acquire(150);assert(c);c->poisoned=false;xsr_release();
 mode=2;assert(xsr_watch_next_status(1,50,&unusable)==-ETIMEDOUT&&unusable&&!locked);
 assert(opens==1);puts("PASS original/checked watch, lock refusal, poisoned timeout, closed session, single owner");return 0;
}
''')
            cc=shutil.which('cc');self.assertIsNotNone(cc)
            runtime=ROOT/'platform/guests/zephyr/xen-runtime'
            store=ROOT/'platform/guests/zephyr/xen-store'
            for name,flags in [('native',[]),('sanitized',['-fsanitize=address,undefined','-fno-omit-frame-pointer','-no-pie'])]:
                binary=home/name
                argv=[cc,'-std=c11','-Wall','-Wextra','-Werror','-g',
                      '-I'+str(home),'-I'+str(runtime/'include'),'-I'+str(store/'include'),
                      str(runtime/'src/runtime.c'),str(source),'-o',str(binary),*flags]
                r=subprocess.run(argv,capture_output=True,text=True,timeout=30)
                self.assertEqual(r.returncode,0,r.stdout+r.stderr)
                r=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
                self.assertEqual(r.returncode,0,r.stdout+r.stderr)
                self.assertIn('PASS original/checked watch',r.stdout)


if __name__=='__main__':unittest.main()
