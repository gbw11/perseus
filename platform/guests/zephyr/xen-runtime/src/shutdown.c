/* SPDX-License-Identifier: Apache-2.0 */
/* One-shot monitor. Local unwind and an external request compete for cleanup;
 * neither may close another active user's shared Store session. */
#include <xenova/shutdown.h>
#include <xenova/shutdown_flow.h>
#include <xenova/runtime.h>
#include <xenova/netfront.h>
#include <zephyr/arch/arm64/hypercall.h>
#include <zephyr/xen/public/sched.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/printk.h>
#include <errno.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
BUILD_ASSERT(CONFIG_XENOVA_SHUTDOWN_THREAD_PRIORITY<CONFIG_NUM_PREEMPT_PRIORITIES,"invalid shutdown priority");
static xshutdown_hook app_hook;
static void *app_context;
static uint32_t watch_id;
static bool started, launched;
static atomic_t stopping, joined, watch_owned, features_possible;
/* 0: available, 1: external request (never reset), 2: local unwind. */
static atomic_t owner, initial_error, cleanup_error;
static char shutdown_path[XS_PATH_LIMIT],domain_path[64];
static struct k_thread thread;
K_MUTEX_DEFINE(shutdown_lock);
K_SEM_DEFINE(feature_publication_done,0,1);
static struct xsd_flow flow;
K_THREAD_STACK_DEFINE(shutdown_stack,4096);
static int remaining(int64_t end,int cap) { return MIN(cap,MAX(0,end-k_uptime_get())); }
static int64_t now(void *p) { ARG_UNUSED(p);return k_uptime_get(); }
static int ack(void *p,int64_t end) {
 ARG_UNUSED(p);return xsr_write(shutdown_path,"",remaining(end,100));
}
static int quiesce(void *p,const char *request,int64_t end) {
 ARG_UNUSED(p);int rc=app_hook(request,end,app_context);
 printk("XENOVA quiesce request=%s rc=%d\n",request,rc);return rc;
}
static int net_close(void *p,int64_t end) {
 ARG_UNUSED(p);
#if defined(CONFIG_XENOVA_NETFRONT)
 return xnet_close(end);
#else
 ARG_UNUSED(end);return 0;
#endif
}
static int disable_features(int64_t end) {
 if(!atomic_get(&features_possible))return 0;
 char path[XS_PATH_LIMIT];int first=0;
 const char *keys[]={"feature-poweroff","feature-reboot"};
 for(unsigned i=0;i<ARRAY_SIZE(keys);i++) {
  snprintf(path,sizeof(path),"%s/control/%s",domain_path,keys[i]);
  int rc=xsr_write(path,"0",remaining(end,100));
  if(rc&&!first)first=rc;
 }
 if(!first)atomic_clear(&features_possible);
 return first;
}
static int remove_watch(int64_t end) {
 if(!atomic_get(&watch_owned))return 0;
 int rc=xsr_unwatch(watch_id,end);
 if(!rc||rc==-ENOENT){atomic_clear(&watch_owned);return 0;}
 return rc;
}
static int store_close(void *p,int64_t end) {
 ARG_UNUSED(p);
 int first=disable_features(end),rc=remove_watch(end);
 if(!first)first=rc;
 return first?first:xsr_close(end);
}
static int terminate(void *p,unsigned reason) {
 ARG_UNUSED(p);struct sched_shutdown op={.reason=reason};
 BUILD_ASSERT(SHUTDOWN_poweroff==0&&SHUTDOWN_reboot==1,"fixed Xen shutdown ABI");
 return HYPERVISOR_sched_op(SCHEDOP_shutdown,&op);
}
static void watcher(void *a,void *b,void *c) {
 ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);
 /* All paths after creation release this barrier, including publication failure. */
 k_sem_take(&feature_publication_done,K_FOREVER);
 bool dirty=true;
 while(!atomic_get(&stopping)) {
  if(dirty) {
   char request[32];int rc=xsr_read(shutdown_path,request,sizeof(request),100);
   if(!rc&&request[0]&&(!strcmp(request,"poweroff")||!strcmp(request,"reboot"))&&
      atomic_cas(&owner,0,1)) {
    int64_t end=k_uptime_get()+CONFIG_XENOVA_SHUTDOWN_BUDGET_MS;
    rc=xsd_run(&flow,request,end);
    int withdraw=disable_features(end); /* Same request budget, no extra window. */
    atomic_set(&cleanup_error,rc?rc:withdraw);
    printk("XENOVA shutdown phase=%d rc=%d withdrawal=%d; removal must be observed by dom0\n",flow.phase,rc,withdraw);
    break; /* Failed/returning requests must not become new physical jobs. */
   }
   dirty=false;
  }
  if(!atomic_get(&stopping)&&!xsr_watch_next(watch_id,10))dirty=true;
  k_sleep(K_MSEC(10));
 }
}
int xshutdown_start(xshutdown_hook hook,void *context) {
 if(!hook)return -EINVAL;
 if(k_mutex_lock(&shutdown_lock,K_MSEC(1000)))return -ETIMEDOUT;
 if(started||atomic_get(&owner)){k_mutex_unlock(&shutdown_lock);return -EALREADY;}
 started=true;
 int rc=xsr_open();if(rc)goto out;
 struct xs_client *c=xsr_acquire(k_uptime_get()+1000);
 if(!c){rc=-ETIMEDOUT;goto out;}
 char domid[16],*tail;unsigned long id;
 rc=xs_read_text(c,0,"domid",domid,sizeof(domid),500);
 if(!rc) {
  id=strtoul(domid,&tail,10);
  if(!domid[0]||*tail||id==0||id>0x7fef)rc=-EPROTO;
  else {
   snprintf(domain_path,sizeof(domain_path),"/local/domain/%lu",id);
   snprintf(shutdown_path,sizeof(shutdown_path),"%s/control/shutdown",domain_path);
   rc=xs_watch(c,shutdown_path,&watch_id,500);
   if(watch_id)atomic_set(&watch_owned,1); /* Includes XS_W_UNCERTAIN. */
  }
 }
 xsr_release();if(rc)goto out;
 app_hook=hook;app_context=context;
 flow.ops=(struct xsd_ops){now,ack,quiesce,net_close,store_close,terminate,NULL};
 launched=true;
 k_thread_create(&thread,shutdown_stack,K_THREAD_STACK_SIZEOF(shutdown_stack),watcher,
                 NULL,NULL,NULL,CONFIG_XENOVA_SHUTDOWN_THREAD_PRIORITY,0,K_NO_WAIT);
 char feature[XS_PATH_LIMIT];
 atomic_set(&features_possible,1); /* Error can follow a successful remote write. */
 snprintf(feature,sizeof(feature),"%s/control/feature-poweroff",domain_path);
 rc=xsr_write(feature,"1",500);
 snprintf(feature,sizeof(feature),"%s/control/feature-reboot",domain_path);
 if(!rc)rc=xsr_write(feature,"1",500);
 if(rc)atomic_set(&stopping,1);
 k_sem_give(&feature_publication_done);
out:
 atomic_set(&initial_error,rc);
 k_mutex_unlock(&shutdown_lock);return rc;
}
int xshutdown_stop(int64_t end) {
 int64_t left=end-k_uptime_get();if(left<=0)return -ETIMEDOUT;
 if(k_mutex_lock(&shutdown_lock,K_MSEC(left)))return -ETIMEDOUT;
 if(atomic_get(&owner)==1){k_mutex_unlock(&shutdown_lock);return -EBUSY;}
 if(!atomic_cas(&owner,0,2)&&atomic_get(&owner)!=2){k_mutex_unlock(&shutdown_lock);return -EBUSY;}
 atomic_set(&stopping,1);k_sem_give(&feature_publication_done);
 int first=0;
 if(launched&&!atomic_get(&joined)) {
  left=end-k_uptime_get();
  if(k_thread_join(&thread,left>0?K_MSEC(left):K_NO_WAIT))first=-ETIMEDOUT;
  else atomic_set(&joined,1);
 }
 /* A live receiver can still consume this watch. Never remove it before join. */
 if(!first) {
  first=disable_features(end);int rc=remove_watch(end);if(!first)first=rc;
 }
 if(first)atomic_cas(&cleanup_error,0,first);
 k_mutex_unlock(&shutdown_lock);return first;
}
int xshutdown_status_get(struct xshutdown_status *out) {
 if(!out)return -EINVAL;
 if(k_mutex_lock(&shutdown_lock,K_NO_WAIT))return -EBUSY;
 *out=(struct xshutdown_status){started,!launched||atomic_get(&joined),
  atomic_get(&watch_owned),atomic_get(&features_possible),atomic_get(&owner)==1,
  atomic_get(&initial_error),atomic_get(&cleanup_error)};
 k_mutex_unlock(&shutdown_lock);return 0;
}
