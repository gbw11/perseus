/* Actual TX owner holds the production mutex; kernel timeouts/poll/close are real. */
#include <lifecycle_boundary.h>
#include <xenova/netfront.h>
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

K_THREAD_STACK_DEFINE(sender_stack,4096);
static struct k_thread sender;
static struct net_pkt packet={.length=14};
static int send_result;
static void send_packet(void *a,void *b,void *c) {
 ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);send_result=xl_api->send(NULL,&packet);
}
static struct xnet_status status(void) {
 struct xnet_status s;assert(!xnet_status_get(&s));return s;
}
static void hold(void) {
 assert(k_thread_create(&sender,sender_stack,K_THREAD_STACK_SIZEOF(sender_stack),
  send_packet,NULL,NULL,NULL,8,0,K_NO_WAIT));
 assert(!k_sem_take(&xl_acquired,K_SECONDS(1)));
}
static void release(void) {
 k_sem_give(&xl_release);assert(!k_thread_join(&sender,K_SECONDS(1)));assert(!send_result);
}
static void reaped(unsigned count) {
 int64_t end=k_uptime_get()+1000;
 do {
  if(atomic_load(&xl_tx_done)==count)return;k_sleep(K_MSEC(1));
 }while(k_uptime_get()<end);
 assert(!"completion not processed after mutex release");
}
static struct xnet_status observed(int kind) {
 int64_t end=k_uptime_get()+1000;struct xnet_status s;
 do{s=status();if(s.backend_observation==kind)return s;k_sleep(K_MSEC(1));}while(k_uptime_get()<end);
 assert(!"backend observation not reached");return s;
}
static void link_stopped(void) {
 int64_t end=k_uptime_get()+1000;
 do{if(!status().connected)return;k_sleep(K_MSEC(1));}while(k_uptime_get()<end);
 assert(!"terminal error did not stop TX");
}
static void finished(unsigned grants) {
 struct xnet_status s=status();struct xnet_waits w;assert(!xnet_waits_get(&w));
 assert(s.closed&&s.worker_joined&&s.monitor_joined&&xl_revokes==grants);
 assert(xnet_start()==-EALREADY);
 printf("PACKET_RESULT {\"timeouts\":%llu,\"first_lock_error\":%d,\"last_poll_ms\":%llu,\"wait_max_us\":%llu,\"hold_max_us\":%llu}\n",
  (unsigned long long)w.packet_lock_timeouts,w.packet_lock_first_error,
  (unsigned long long)w.packet_last_poll_ms,(unsigned long long)w.packet_lock_wait_max_us,
  (unsigned long long)w.packet_lock_hold_max_us);
 puts("PASS packet mutex contention preserves worker, TX ownership and normal close");exit(0);
}
int main(void) {
 xl_case=getenv("XL_CASE");assert(xl_case);xl_net_init();assert(!xnet_start());
 const char *scenario=xl_case;
 unsigned grants=xl_grants;k_sleep(K_MSEC(20));hold();unsigned polls=atomic_load(&xl_polls);
 struct xnet_waits prior;assert(!xnet_waits_get(&prior));int64_t began=k_uptime_get();
 k_sleep(K_MSEC(!strcmp(scenario,"packet-long")?1000:100));struct xnet_status held=status();
 printf("PACKET_HELD connected=%d poll_delta=%u grants=%u\n",held.connected,atomic_load(&xl_polls)-polls,xl_grants);
 assert(held.connected&&!held.backend_error&&!xl_revokes&&xl_grants==grants);
 assert(atomic_load(&xl_polls)==polls);
 struct xnet_waits delayed;assert(!xnet_waits_get(&delayed));
 assert(delayed.packet_lock_timeouts&&delayed.packet_lock_first_error==-EAGAIN);
 assert(delayed.packet_last_poll_ms==prior.packet_last_poll_ms);
 assert(delayed.packet_lock_timeouts<=(uint64_t)(k_uptime_get()-began)/20+2);
 /* Snapshot reads never wait behind the stalled packet owner. */
 struct xnf_stats stats;assert(xnet_stats_try(&stats)==-EBUSY);
 /* Another sender loses only its own bounded attempt. */
 assert(xl_api->send(NULL,&packet)==-ETIMEDOUT&&status().connected);
 if(!strcmp(scenario,"packet-close")){
  int64_t begin=k_uptime_get(),end=begin+60;
  assert(xnet_close(end)==-ETIMEDOUT);int64_t elapsed=k_uptime_get()-begin;
  held=status();assert(held.stopping&&!held.closed&&held.grants_held==grants&&!xl_revokes);
  assert(elapsed>=60&&elapsed<=80); /* native 10ms tick allowance, not a target bound */
  assert(xl_api->send(NULL,&packet)==-ENETDOWN);
  release();xl_complete_tx();xl_case="net-ok";assert(!xnet_close(k_uptime_get()+1000));
  assert(status().cleanup_error==-ETIMEDOUT);finished(grants);
 }
 if(!strcmp(scenario,"packet-stop")){
  xnet_stop_request();assert(xl_api->send(NULL,&packet)==-ENETDOWN);
  release();xl_complete_tx();reaped(1); /* stop seals producers, not completion drain */
  assert(status().stopping&&!status().connected);xl_case="net-ok";
  assert(!xnet_close(k_uptime_get()+1000));finished(grants);
 }
 if(!strcmp(scenario,"packet-closing")||!strcmp(scenario,"packet-kernel-error")){
  if(!strcmp(scenario,"packet-closing")){
   xl_case="wait-closing";xl_wait_enabled=true;assert(!observed(3).connected);
  }else{atomic_store(&xl_packet_lock_error,-EIO);link_stopped();}
  unsigned stopped=atomic_load(&xl_polls);release();xl_complete_tx();k_sleep(K_MSEC(40));
  assert(atomic_load(&xl_polls)==stopped&&xl_grants==grants&&!xl_revokes);
  assert(xl_api->send(NULL,&packet)==-ENETDOWN);xl_wait_enabled=false;xl_case="net-ok";
  assert(!xnet_close(k_uptime_get()+1000));finished(grants);
 }
 if(!strcmp(scenario,"packet-store")){
  xl_case="wait-timeout";xl_wait_enabled=true;assert(observed(2).connected);
 }
 if(!strcmp(scenario,"packet-corrupt")){
  release();xl_corrupt_tx();link_stopped();
  assert(atomic_load(&xl_poll_error)==-EPROTO&&!atomic_load(&xl_tx_done));
  assert(xnet_stats(&stats)==-ENETDOWN&&xl_api->send(NULL,&packet)==-ENETDOWN);
  assert(!xl_revokes);xl_case="net-ok";assert(xnet_close(k_uptime_get()+1000)==-ENETDOWN);
  assert(atomic_load(&xl_poll_error)==-EPROTO&&status().cleanup_error==-ENETDOWN);
  assert(!xnet_close(k_uptime_get()+1000));finished(grants);
 }
 int64_t released=k_uptime_get();release();xl_complete_tx();reaped(1);
 printf("PACKET_RECOVERY elapsed_ms=%lld\n",(long long)(k_uptime_get()-released));
 assert(status().connected&&xl_grants==grants&&!xl_revokes);
 if(!strcmp(scenario,"packet-store")){
  xl_wait_enabled=false;assert(observed(1).connected);
  assert(status().first_observation_error==-ETIMEDOUT);
 }
 unsigned total=1;
 if(!strcmp(scenario,"packet-repeat")){
  for(unsigned i=0;i<2;i++){
   struct xnet_waits prior;assert(!xnet_waits_get(&prior));hold();k_sleep(K_MSEC(100));
   assert(!xnet_waits_get(&delayed)&&delayed.packet_lock_timeouts>prior.packet_lock_timeouts);
   release();xl_complete_tx();reaped(++total);
  }
 }
 assert(!xnet_waits_get(&delayed)&&delayed.packet_polls>polls&&delayed.packet_last_poll_ms);
 xl_case="net-ok";assert(!xl_api->send(NULL,&packet));xl_complete_tx();
 reaped(total+1);assert(!xnet_close(k_uptime_get()+1000));finished(grants);
}
