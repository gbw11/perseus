/* SPDX-License-Identifier: Apache-2.0 */
/* Shared Store watcher is independent of DHCP/netfront and invokes no app hook
 * under Store locks or in interrupt context. Feature publication is not readiness
 * of a driving application and does not grant vehicle control. */
#include <xenova/shutdown.h>
#include <xenova/shutdown_flow.h>
#include <xenova/runtime.h>
#include <xenova/netfront.h>
#include <zephyr/arch/arm64/hypercall.h>
#include <zephyr/xen/public/sched.h>
#include <zephyr/sys/printk.h>
#include <errno.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
BUILD_ASSERT(CONFIG_XENOVA_SHUTDOWN_THREAD_PRIORITY<CONFIG_NUM_PREEMPT_PRIORITIES,"invalid shutdown priority");
static xshutdown_hook app_hook;static void *app_context;
static uint32_t watch_id;static bool launched;
static char shutdown_path[XS_PATH_LIMIT],domain_path[64];
static struct k_thread thread;
K_SEM_DEFINE(feature_publication_done,0,1);
static struct xsd_flow flow;
K_THREAD_STACK_DEFINE(shutdown_stack,4096);
static int64_t now(void *p){ARG_UNUSED(p);return k_uptime_get();}
static int ack(void *p,int64_t end){
 ARG_UNUSED(p);return xsr_write(shutdown_path,"",MIN(100,MAX(0,end-k_uptime_get())));
}
static int quiesce(void *p,const char *request,int64_t end){
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
 char feature[XS_PATH_LIMIT];
 snprintf(feature,sizeof(feature),"%s/control/feature-poweroff",domain_path);
 int rc=xsr_write(feature,"0",MIN(100,MAX(0,end-k_uptime_get())));
 snprintf(feature,sizeof(feature),"%s/control/feature-reboot",domain_path);
 if(!rc)rc=xsr_write(feature,"0",MIN(100,MAX(0,end-k_uptime_get())));
 return rc;
}
static int store_close(void *p,int64_t end) {
 ARG_UNUSED(p);
 /* The handler cannot service another request once its session is closed. */
 int rc=disable_features(end);
 return rc?rc:xsr_close(end);
}
static int terminate(void *p,unsigned reason){
 ARG_UNUSED(p);struct sched_shutdown op={.reason=reason};
 BUILD_ASSERT(SHUTDOWN_poweroff==0&&SHUTDOWN_reboot==1,"fixed Xen shutdown ABI");
 return HYPERVISOR_sched_op(SCHEDOP_shutdown,&op);
}
static void watcher(void *a,void *b,void *c) {
 ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);
 /* Startup publication must finish before a pending request can fail/withdraw
  * features. Otherwise the starter could republish readiness after failure. */
 k_sem_take(&feature_publication_done,K_FOREVER);
 bool dirty=true;
 for(;;) {
  if(dirty) {
   char request[32];int rc=xsr_read(shutdown_path,request,sizeof(request),100);
   if(!rc&&request[0]){
    rc=xsd_run(&flow,request,k_uptime_get()+CONFIG_XENOVA_SHUTDOWN_BUDGET_MS);
    if(flow.phase==XSD_REJECTED||flow.phase==XSD_TIMED_OUT||flow.phase==XSD_FAILED)
     (void)disable_features(k_uptime_get()+200);
    printk("XENOVA shutdown phase=%d rc=%d; removal must be observed by dom0\n",flow.phase,rc);
   }
   dirty=false;
  }
  int rc=xsr_watch_next(watch_id,10);
  if(!rc)dirty=true;
  k_sleep(K_MSEC(10));
 }
}
int xshutdown_start(xshutdown_hook hook,void *context) {
 if(!hook||launched)return -EINVAL;
 int rc=xsr_open();if(rc)return rc;
 struct xs_client *c=xsr_acquire(k_uptime_get()+1000);if(!c)return -ETIMEDOUT;
 char domid[16],*tail;unsigned long id;
 rc=xs_read_text(c,0,"domid",domid,sizeof(domid),500);
 if(!rc){
  id=strtoul(domid,&tail,10);
  if(!domid[0]||*tail||id==0||id>0x7fef)rc=-EPROTO;
  else {
   snprintf(domain_path,sizeof(domain_path),"/local/domain/%lu",id);
   snprintf(shutdown_path,sizeof(shutdown_path),"%s/control/shutdown",domain_path);
   rc=xs_watch(c,shutdown_path,&watch_id,500);
  }
 }
 xsr_release();if(rc)return rc;
 app_hook=hook;app_context=context;launched=true;
 flow.ops=(struct xsd_ops){now,ack,quiesce,net_close,store_close,terminate,NULL};
 /* Watch first, publish only with initialized hook/worker, then initial reread. */
 k_thread_create(&thread,shutdown_stack,K_THREAD_STACK_SIZEOF(shutdown_stack),watcher,
                 NULL,NULL,NULL,CONFIG_XENOVA_SHUTDOWN_THREAD_PRIORITY,0,K_NO_WAIT);
 char feature[XS_PATH_LIMIT];
 snprintf(feature,sizeof(feature),"%s/control/feature-poweroff",domain_path);
 rc=xsr_write(feature,"1",500);
 snprintf(feature,sizeof(feature),"%s/control/feature-reboot",domain_path);
 if(!rc)rc=xsr_write(feature,"1",500);
 if(rc){
  (void)disable_features(k_uptime_get()+200);
  printk("XENOVA shutdown feature publication failed rc=%d; no ACL escalation\n",rc);
 }
 k_sem_give(&feature_publication_done);
 return rc;
}
