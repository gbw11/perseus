/* Product packet/observer threads; only Xen/Store/device boundaries are fixtures. */
#include <lifecycle_boundary.h>
#include <xenova/netfront.h>
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static struct xnet_status observed(int expected) {
 struct xnet_status status;int64_t end=k_uptime_get()+2000;
 do {
  assert(!xnet_status_get(&status));
  if(status.backend_observation==expected)return status;
  k_sleep(K_MSEC(1));
 }while(k_uptime_get()<end);
 assert(!"expected backend observation not reached");return status;
}
static void tx_reaped(uint64_t count) {
 struct xnf_stats stats;int64_t end=k_uptime_get()+1000;
 do {
  assert(!xnet_stats(&stats));if(stats.tx==count)return;
  k_sleep(K_MSEC(1));
 }while(k_uptime_get()<end);
 assert(!"TX completion not reaped");
}
int main(void) {
 xl_case=getenv("XL_CASE");assert(xl_case);xl_net_init();assert(!xnet_start());
 unsigned grants=xl_grants;struct net_pkt packet={.length=14};
 xl_wait_enabled=true;assert(!k_sem_take(&xl_store_wait,K_SECONDS(1)));
 bool fatal=!strcmp(xl_case,"wait-poison")||!strcmp(xl_case,"wait-watch-poison")||
  !strcmp(xl_case,"wait-closing")||!strcmp(xl_case,"wait-closed")||!strcmp(xl_case,"wait-identity")||!strcmp(xl_case,"wait-protocol");
 if(fatal){
  struct xnet_status broken=observed(!strcmp(xl_case,"wait-closing")||!strcmp(xl_case,"wait-closed")?3:4);
  int expected=!strcmp(xl_case,"wait-identity")?-EXDEV:!strcmp(xl_case,"wait-protocol")?-EPROTO:
   !strcmp(xl_case,"wait-poison")||!strcmp(xl_case,"wait-watch-poison")?-ETIMEDOUT:-ENETDOWN;
  assert(broken.backend_error==expected&&!broken.connected&&broken.first_observation_error==expected);
  assert(xl_api->send(NULL,&packet)==-ENETDOWN);xl_wait_enabled=false;
  k_sleep(K_MSEC(300));assert(xl_api->send(NULL,&packet)==-ENETDOWN&&xl_grants==grants);
  bool poisoned=!strcmp(xl_case,"wait-poison")||!strcmp(xl_case,"wait-watch-poison");
  int rc=xnet_close(k_uptime_get()+300);struct xnet_status done;assert(!xnet_status_get(&done));
  if(poisoned){assert(rc==-ETIMEDOUT&&!done.closed&&done.grants_held==grants&&!xl_revokes);}
  else assert(!rc&&done.closed&&xl_revokes==grants);
  puts("PASS terminal backend/identity/session failure never auto-reconnects or frees uncertain grants");exit(0);
 }
 struct xnet_status uncertain=observed(2);
 int expected=!strcmp(xl_case,"wait-busy")||!strcmp(xl_case,"wait-watch-busy")?-EBUSY:
  !strcmp(xl_case,"wait-again")?-EAGAIN:-ETIMEDOUT;
 assert(uncertain.connected&&!uncertain.backend_error&&uncertain.observation_error==expected&&uncertain.first_observation_error==expected);
 unsigned before=atomic_load(&xl_polls);k_sleep(K_MSEC(30));
 assert(atomic_load(&xl_polls)>before);
 if(getenv("XL_CLOSE_UNAVAILABLE")){
  xnet_stop_request();assert(xnet_close(k_uptime_get()+300)==-ETIMEDOUT);
  struct xnet_status held;assert(!xnet_status_get(&held));
  assert(held.stopping&&!held.closed&&!held.connected&&held.grants_held==grants&&!xl_revokes);
  assert(xl_api->send(NULL,&packet)==-ENETDOWN);xl_wait_enabled=false;
  assert(!xnet_close(k_uptime_get()+1000));assert(!xnet_status_get(&held));
  assert(held.closed&&!held.connected&&xl_revokes==grants);
  puts("PASS unavailable shutdown keeps grants, then closes without reopening TX");exit(0);
 }
 assert(!xl_api->send(NULL,&packet));xl_complete_tx();tx_reaped(1);
 struct xnet_waits failed;assert(!xnet_waits_get(&failed));assert(failed.store_unavailable>0);
 if(getenv("XL_REPEAT")){
  unsigned polls=atomic_load(&xl_polls);uint64_t calls=failed.store_observations;
  k_sleep(K_MSEC(1100));assert(!xnet_waits_get(&failed));
  assert(failed.store_observations-calls>=3&&failed.store_observations-calls<=6);
  assert(atomic_load(&xl_polls)>polls+10&&xl_grants==grants&&!xl_revokes);
 }
 /* Slot pressure is a bounded send refusal, not a link transition. */
 for(unsigned k=0;k<XNF_SLOTS;k++)assert(!xl_api->send(NULL,&packet));
 assert(xl_api->send(NULL,&packet)==-ENOBUFS);xl_complete_tx();tx_reaped(1+XNF_SLOTS);
 xl_wait_enabled=false;struct xnet_status recovered=observed(1);
 assert(recovered.connected&&!recovered.backend_error&&!recovered.observation_error);
 assert(recovered.first_observation_error==uncertain.first_observation_error);
 struct xnet_waits restored;assert(!xnet_waits_get(&restored));assert(restored.store_recoveries>0);
 assert(!xl_api->send(NULL,&packet));xl_complete_tx();tx_reaped(2+XNF_SLOTS);
 assert(xl_grants==grants); /* Observation recovery creates no second VIF/grant set. */
 if(getenv("XL_STOP_RACE")){
  xl_case="wait-stop-race";xl_wait_enabled=true;
  assert(!k_sem_take(&xl_acquired,K_SECONDS(1)));xnet_stop_request();
  assert(!xl_revokes);k_sem_give(&xl_release);k_sleep(K_MSEC(20));
  struct xnet_status stopped;assert(!xnet_status_get(&stopped));
  assert(stopped.stopping&&!stopped.connected&&xl_api->send(NULL,&packet)==-ENETDOWN);
  xl_wait_enabled=false;
 }
 assert(!xnet_close(k_uptime_get()+1000));
 struct xnet_status done;assert(!xnet_status_get(&done));
 assert(done.closed&&done.worker_joined&&done.monitor_joined&&xl_revokes==grants);
 printf("POLICY_RESULT {\"unavailable\":%llu,\"recoveries\":%llu,\"observations\":%llu,\"polls\":%u}\n",
  (unsigned long long)failed.store_unavailable,(unsigned long long)restored.store_recoveries,
  (unsigned long long)failed.store_observations,atomic_load(&xl_polls));
 puts("PASS transient observation keeps packet progress, recovers and closes owned resources");exit(0);
}
