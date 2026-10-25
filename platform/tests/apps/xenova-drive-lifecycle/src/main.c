/* Product drive runtime/Core/v1/Publisher, real kernel, external Xen boundary. */
#include <xenova/drive_runtime.h>
#include <xenova/telemetry.h>
#include <xenova/shutdown.h>
#include <xenova/netfront.h>
#include <assert.h>
#include <errno.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
static const char *scenario;
static xshutdown_hook hook;
static bool external,stopped;
static unsigned publisher_starts;
static int hook_result,start_result;
static struct xm_zclient client;
K_SEM_DEFINE(in_network,0,1);
K_SEM_DEFINE(release_network,0,1);
K_THREAD_STACK_DEFINE(task_stack,4096);
K_THREAD_STACK_DEFINE(hook_stack,4096);
static struct k_thread task,handler;
int xl_real_xt_start(xt_bootstrap_loader,void *,const struct xt_inventory *);
int xt_start(xt_bootstrap_loader loader,void *ctx,const struct xt_inventory *info) {
 publisher_starts++;
 return !strcmp(scenario,"publisher")?-EIO:xl_real_xt_start(loader,ctx,info);
}
int xt_xen_bootstrap_load(struct xt_bootstrap *b,int64_t end,void *c) {
 ARG_UNUSED(b);ARG_UNUSED(end);ARG_UNUSED(c);
 if(!strcmp(scenario,"deadline-io")){k_sem_give(&in_network);k_sleep(K_MSEC(250));}
 return -EAGAIN;
}
int xshutdown_start(xshutdown_hook h,void *ctx) {
 ARG_UNUSED(ctx);hook=h;return !strcmp(scenario,"shutdown")?-EIO:0;
}
int xshutdown_stop(int64_t end) { if(end<=k_uptime_get())return -ETIMEDOUT;return external?-EBUSY:0; }
void xnet_stop_request(void) { stopped=true; }
int xnet_start(void) {
 if(!strcmp(scenario,"race")){k_sem_give(&in_network);k_sem_take(&release_network,K_FOREVER);}
 if(!strcmp(scenario,"cleanup-error"))return -ENOMEM;
 return !strcmp(scenario,"network")?-ENETDOWN:0;
}
int xnet_close(int64_t end) { assert(end>k_uptime_get());return !strcmp(scenario,"cleanup-error")?-EIO:0; }
int xsr_close(int64_t end) { assert(end>k_uptime_get());return 0; }
int xnet_stats(struct xnf_stats *s) { ARG_UNUSED(s);return -EAGAIN; }
static void start(void *a,void *b,void *c) {
 ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);start_result=xd_start("manual",20);
}
static void quiesce(void *a,void *b,void *c) {
 ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);external=true;
 hook_result=hook("reboot",k_uptime_get()+(!strcmp(scenario,"deadline-io")?50:1000),NULL);
}
int main(void) {
 scenario=getenv("XL_CASE");assert(scenario);
 if(!strcmp(scenario,"race")) {
  k_thread_create(&task,task_stack,K_THREAD_STACK_SIZEOF(task_stack),start,NULL,NULL,NULL,8,0,K_NO_WAIT);
  assert(!k_sem_take(&in_network,K_SECONDS(1)));
  k_thread_create(&handler,hook_stack,K_THREAD_STACK_SIZEOF(hook_stack),quiesce,NULL,NULL,NULL,6,0,K_NO_WAIT);
  k_sleep(K_MSEC(1));assert(stopped);k_sem_give(&release_network);
  assert(!k_thread_join(&task,K_SECONDS(1)));assert(start_result==-ECANCELED&&!publisher_starts);
  assert(!k_thread_join(&handler,K_SECONDS(1)));assert(!hook_result);
  assert(xd_finish(&client,start_result)==start_result);
 } else {
  int rc=xd_start("manual",!strcmp(scenario,"loop")?0:20);
  bool normal=!strcmp(scenario,"normal")||!strcmp(scenario,"normal-io")||!strcmp(scenario,"deadline-io");assert(normal?rc==0:rc<0);
  if(!strcmp(scenario,"normal-io")||!strcmp(scenario,"deadline-io"))assert(!xd_client_prepare(&client,"control","input"));
  if(!strcmp(scenario,"deadline-io")) {
   assert(!k_sem_take(&in_network,K_SECONDS(1)));
   k_thread_create(&handler,hook_stack,K_THREAD_STACK_SIZEOF(hook_stack),quiesce,NULL,NULL,NULL,6,0,K_NO_WAIT);
   k_sleep(K_MSEC(1));assert(xd_stopping());int64_t began=k_uptime_get();
   int finish=xd_finish(&client,0);int64_t elapsed=k_uptime_get()-began;
   printf("IO_DEADLINE finish=%d elapsed_ms=%lld\n",finish,(long long)elapsed);
   assert(finish==-ETIMEDOUT&&elapsed<=60);
   assert(!k_thread_join(&handler,K_SECONDS(1)));assert(hook_result==-ETIMEDOUT);
   struct xd_io_stats s;assert(!xd_io_stats_get(&s));assert(s.started&&!s.joined);
   assert(!xd_io_join(k_uptime_get()+1000));assert(!xd_io_stats_get(&s));assert(s.joined);
   puts("PASS shared external deadline; late owner retained and joined without replay");exit(0);
  }
  if(!strcmp(scenario,"cleanup-error")) {
   assert(rc==-ENOMEM&&xd_finish(&client,rc)==-ENOMEM);
   static struct xdiag_snapshot snapshot;assert(!xdiag_snapshot_get(&snapshot,false));
   bool initial=false,cleanup=false;
   for(size_t i=0;i<snapshot.history.error_entries;i++) {
    struct xdiag_event *e=&snapshot.history.errors[i];
    initial|=!strcmp(e->stage,"drive-init")&&e->code==-ENOMEM;
    cleanup|=!strcmp(e->stage,"drive-cleanup")&&e->code==-EIO;
   }
   assert(initial&&cleanup);
  } else if(!normal) {
   /* No app loop was started, and main has NOT given app_done. */
   external=true;int64_t begin=k_uptime_get();
   assert(!hook("reboot",begin+100,NULL));assert(k_uptime_get()-begin<100);
   assert(xd_finish(&client,rc)==rc);
  } else {
   k_thread_create(&handler,hook_stack,K_THREAD_STACK_SIZEOF(hook_stack),quiesce,NULL,NULL,NULL,6,0,K_NO_WAIT);
   k_sleep(K_MSEC(1));assert(xd_stopping());assert(!xd_finish(&client,0));
   assert(!k_thread_join(&handler,K_SECONDS(1)));assert(!hook_result);
   assert(xd_client_prepare(&client,"control","input")==-ESHUTDOWN);
   assert(xd_publish(&client,"events","app.heartbeat","{}")==-ESHUTDOWN);
   assert(xd_poll(&client,20)==-ESHUTDOWN);
   assert(!client.connection_attempts&&!client.message_counter);
   if(!strcmp(scenario,"normal-io")) {
    struct xd_io_stats s;assert(!xd_io_stats_get(&s));assert(s.started&&s.joined);
   }
  }
 }
 assert(xd_start("manual",20)==-EALREADY);
 printf("PASS actual drive lifecycle %s; no unstarted app_done wait, publisher starts=%u\n",scenario,publisher_starts);
 exit(0);
}
