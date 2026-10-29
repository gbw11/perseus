/* Real packet/state threads; only the existing Store boundary is delayed. */
#include <lifecycle_boundary.h>
#include <xenova/netfront.h>
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
int main(void) {
 xl_case=getenv("XL_CASE");assert(xl_case);xl_net_init();assert(!xnet_start());
 xl_wait_enabled=true;
 assert(!k_sem_take(&xl_store_wait,K_SECONDS(1)));
 if(!strcmp(xl_case,"wait-delay")) {
  unsigned before=atomic_load(&xl_polls);int64_t start=k_uptime_get();
  k_sleep(K_MSEC(20));unsigned progress=atomic_load(&xl_polls)-before;
  bool baseline=getenv("XL_EXPECT_BLOCKED")!=NULL;
  assert(baseline?progress==0:progress>0);
  printf("WAIT_RESULT {\"window_ms\":%lld,\"store_delay_ms\":35,\"packet_polls\":%u,\"baseline\":%s}\n",
   (long long)(k_uptime_get()-start),progress,baseline?"true":"false");
 } else {
  k_sleep(K_MSEC(20));struct xnet_status s;assert(!xnet_status_get(&s));
  assert(s.backend_error&&s.backend_observation==(!strcmp(xl_case,"wait-closing")?3:!strcmp(xl_case,"wait-timeout")?2:4));
  struct net_pkt p={.length=14};assert(xl_api->send(NULL,&p)==-ENETDOWN);
  printf("WAIT_RESULT {\"observation\":%d,\"error\":%d}\n",s.backend_observation,s.backend_error);
 }
 xl_wait_enabled=false;assert(!xnet_close(k_uptime_get()+1000));
 struct xnet_status s;assert(!xnet_status_get(&s));assert(s.closed&&s.worker_joined);
 if(!getenv("XL_EXPECT_BLOCKED"))assert(s.monitor_joined);
 puts("PASS Store wait isolation and owned monitor cleanup");exit(0);
}
