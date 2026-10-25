/* Each process is a fresh guest lifetime: one-shot transports never reset. */
#include <lifecycle_boundary.h>
#include <xenova/netfront.h>
#include <xenova/shutdown.h>
#include <assert.h>
#include <errno.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
K_THREAD_STACK_DEFINE(start_stack,4096);
static struct k_thread starter;
static int start_rc;
static unsigned hooks;
K_THREAD_STACK_DEFINE(close_stack,4096);
static struct k_thread closer;
static int close_result;
static void send_packet(void *a,void *b,void *c) {
 ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);
 struct net_pkt packet={.length=14};start_rc=xl_api->send(NULL,&packet);
}
static void close_network(void *a,void *b,void *c) {
 ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);close_result=xnet_close(k_uptime_get()+1000);
}
static void start_thread(void *a,void *b,void *c) { ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);start_rc=xnet_start(); }
static int hook(const char *request,int64_t end,void *ctx) {
 ARG_UNUSED(ctx);assert(!strcmp(request,"reboot"));assert(end>k_uptime_get());assert(xl_features==2);hooks++;
 return strstr(xl_case,"reject")?-EBUSY:strstr(xl_case,"hook")?-EIO:0;
}
int main(void) {
 xl_case=getenv("XL_CASE");assert(xl_case);xl_net_init();
 if(!strncmp(xl_case,"net-",4)) {
  if(!strcmp(xl_case,"net-race")) {
   xl_race=true;
   k_thread_create(&starter,start_stack,K_THREAD_STACK_SIZEOF(start_stack),start_thread,NULL,NULL,NULL,8,0,K_NO_WAIT);
   assert(!k_sem_take(&xl_acquired,K_SECONDS(1)));xnet_stop_request();
   k_sem_give(&xl_release);assert(!k_thread_join(&starter,K_SECONDS(1)));
   assert(start_rc==-ECANCELED&&xl_grants==1);
  } else {
   start_rc=xnet_start();
   bool succeeds=!strcmp(xl_case,"net-ok")||!strcmp(xl_case,"net-busy")||!strcmp(xl_case,"net-close-timeout")||!strcmp(xl_case,"net-send-race");
   assert(succeeds?start_rc==0:start_rc<0);
  }
  if(!strcmp(xl_case,"net-send-race")) {
   k_thread_create(&starter,start_stack,K_THREAD_STACK_SIZEOF(start_stack),send_packet,NULL,NULL,NULL,8,0,K_NO_WAIT);
   assert(!k_sem_take(&xl_acquired,K_SECONDS(1)));
   k_thread_create(&closer,close_stack,K_THREAD_STACK_SIZEOF(close_stack),close_network,NULL,NULL,NULL,6,0,K_NO_WAIT);
   k_sleep(K_MSEC(1));assert(!xl_revokes);k_sem_give(&xl_release);
   assert(!k_thread_join(&starter,K_SECONDS(1)));assert(!start_rc);xl_complete_tx();
   assert(!k_thread_join(&closer,K_SECONDS(1)));assert(!close_result);
   struct net_pkt packet={.length=14};assert(xl_api->send(NULL,&packet)==-ENETDOWN);
  }
  int rc=xnet_close(k_uptime_get()+100);
  struct xnet_status status;assert(!xnet_status_get(&status));
  if(!strcmp(xl_case,"net-busy")||!strcmp(xl_case,"net-close-timeout")||!strcmp(xl_case,"net-backend")) {
   assert(rc<0&&!status.closed&&status.grants_held);
   if(!strcmp(xl_case,"net-busy"))assert(xl_revokes==xl_grants-1&&status.grants_held==1);
   else assert(!xl_revokes&&status.publication_possible);
   xl_retry=true;assert(!xnet_close(k_uptime_get()+1000));
  } else assert(!rc);
  assert(!xnet_status_get(&status));assert(!status.grants_held&&status.event_port==-1&&!status.watch_owned);
  assert(status.worker_joined&&xl_revokes==xl_grants);
  unsigned revokes=xl_revokes;assert(!xnet_close(k_uptime_get()+100));assert(xl_revokes==revokes);
  assert(xnet_start()==-EALREADY);
 } else {
  int rc=xshutdown_start(hook,NULL);
  if(strstr(xl_case,"external")) {
   assert(!rc);struct xshutdown_status s;
   int64_t end=k_uptime_get()+1000;
   do { k_sleep(K_MSEC(1));assert(!xshutdown_status_get(&s)); }while(!s.cleanup_error&&k_uptime_get()<end);
   assert(s.request_owned&&s.cleanup_error);
   unsigned expected=strstr(xl_case,"ack")||strstr(xl_case,"hook")||strstr(xl_case,"reject")||strstr(xl_case,"store")?0:1;
   assert(xl_hypercalls==expected);k_sleep(K_MSEC(50));assert(xl_hypercalls==expected);
   assert(xshutdown_stop(k_uptime_get()+100)==-EBUSY);
  } else {
   assert(rc<0);assert(!xshutdown_stop(k_uptime_get()+1000));
   struct xshutdown_status s;assert(!xshutdown_status_get(&s));
   assert(s.initial_error==rc&&!s.watch_owned&&!s.features_possible&&s.worker_joined);
   assert(!xshutdown_stop(k_uptime_get()+100));assert(!xl_hypercalls&&!hooks&&!xl_closed);
  }
 }
 printf("PASS actual Zephyr kernel lifecycle %s grants=%u revoked=%u hypercalls=%u\n",xl_case,xl_grants,xl_revokes,xl_hypercalls);
 exit(0);
}
