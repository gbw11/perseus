/* SPDX-License-Identifier: Apache-2.0 */
/* Reuse the existing deterministic XenStore peer only at the external boundary. */
#include "xenova_peer.inc"
#include <xenova/runtime.h>
#include <xenova/xenstore_zephyr.h>
#include <pthread.h>
#include <stdatomic.h>
#include <time.h>
static atomic_bool held,release_holder;
static unsigned opened,closed;
int k_mutex_lock(struct k_mutex *m,int64_t ms) {
 struct timespec t;clock_gettime(CLOCK_REALTIME,&t);
 t.tv_sec+=ms/1000;t.tv_nsec+=(ms%1000)*1000000;
 if(t.tv_nsec>=1000000000){t.tv_sec++;t.tv_nsec-=1000000000;}
 int rc=pthread_mutex_timedlock(&m->mutex,&t);return rc?-rc:0;
}
int k_mutex_unlock(struct k_mutex *m){return -pthread_mutex_unlock(&m->mutex);}
int64_t k_uptime_get(void){
 struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (int64_t)t.tv_sec*1000+t.tv_nsec/1000000;
}
int k_is_in_isr(void){return 0;}
int xs_zephyr_open(struct xs_zephyr *z){
 if(opened)return -EALREADY;
 reset();struct xs_io transport=io(&peer);opened++;z->link.open=true;
 return xs_init(&z->link.client,&peer.ring,&transport);
}
int xs_zephyr_close(struct xs_zephyr *z,int ms){
 int rc=xs_close(&z->link.client,ms);closed++;z->link.open=false;return rc;
}
static void *reader(void *p){
 (void)p;
 for(unsigned i=0;i<500;i++){
  char value[16];assert(!xsr_open());assert(!xsr_read("domid",value,sizeof(value),500));
  assert(!strcmp(value,"42"));
 }
 return NULL;
}
static void *holder(void *p){
 (void)p;struct xs_client *c=xsr_acquire(k_uptime_get()+1000);assert(c);
 held=true;while(!release_holder){struct timespec t={.tv_nsec=1000000};nanosleep(&t,NULL);}
 xsr_release();return NULL;
}
static int frontend_publish(struct xs_client *c,uint32_t tx,int64_t end,void *context){
 int rc=xs_write(c,tx,"/local/domain/42/device/vif/0/tx-ring-ref","8",1,xs_remaining(c,end));
 if(!rc)rc=xs_write(c,tx,"/local/domain/42/device/vif/0/state","4",1,xs_remaining(c,end));
 return rc?rc:context?-EIO:0;
}
int main(void){
 assert(!xsr_open());assert(!xsr_open());assert(opened==1);
 /* Real G1 transaction rejects a NULL attempt counter. Shared API supplies it. */
 int writes=peer.writes,commits=peer.commits;
 assert(!xsr_transaction(500,frontend_publish,NULL));
 assert(peer.writes==writes+2&&peer.commits==commits+1);
 assert(!strcmp(get_value(&peer,"/local/domain/42/device/vif/0/state",false),"4"));
 peer.conflicts=1;commits=peer.commits;
 assert(!xsr_transaction(500,frontend_publish,NULL));
 assert(peer.commits==commits+2);
 int aborts=peer.aborts;
 assert(xsr_transaction(500,frontend_publish,&peer)==-EIO);
 assert(peer.aborts==aborts+1);
 assert(xsr_transaction(500,NULL,NULL)==-EINVAL);
 pthread_t a,b;assert(!pthread_create(&a,NULL,reader,NULL));assert(!pthread_create(&b,NULL,reader,NULL));
 pthread_join(a,NULL);pthread_join(b,NULL);assert(opened==1);
 uint32_t shutdown,network;struct xs_client *c=xsr_acquire(k_uptime_get()+1000);assert(c);
 assert(!xs_watch(c,"/local/domain/42/control/shutdown",&shutdown,100));assert(!xs_watch(c,"/backend/state",&network,100));
 xsr_release();
 assert(!xsr_watch_next(shutdown,100));assert(!xsr_watch_next(network,100));
 /* After watch dispatch the app can use Store without waiting for itself. */
 char value[16];assert(!xsr_read("domid",value,sizeof(value),100));
 assert(!pthread_create(&a,NULL,holder,NULL));
 while(!held){struct timespec t={.tv_nsec=1000000};nanosleep(&t,NULL);}
 int64_t start=k_uptime_get();
 assert(xsr_read("domid",value,sizeof(value),20)==-ETIMEDOUT);
 assert(k_uptime_get()-start>=15);
 release_holder=true;
 pthread_join(a,NULL);
 assert(!xsr_read("domid",value,sizeof(value),100));
 assert(!xsr_close(k_uptime_get()+1000));assert(closed==1&&opened==1);
 assert(xsr_open()==-EALREADY);
 puts("production shared Store: 1000 concurrent reads, two watch consumers, bounded lock, one session PASS");
}
