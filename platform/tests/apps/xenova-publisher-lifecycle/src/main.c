/* Real Publisher/Core/kernel. The bounded bootstrap boundary is held by a
 * barrier to test stop timeout, live TCB reuse refusal, and concurrent start. */
#include <xenova/telemetry.h>
#include <xenova/diagnostics.h>
#include <zephyr/kernel.h>
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
K_SEM_DEFINE(entered,0,1);
K_SEM_DEFINE(release_loader,0,1);
K_THREAD_STACK_DEFINE(stop_stack,2048);
static struct k_thread stopper;
static int stop_result;
static bool block_collection;
void xt_test_observation(const struct xdiag_snapshot *raw,const struct xt_observation *out) {
 ARG_UNUSED(raw);ARG_UNUSED(out);
 if(block_collection){k_sem_give(&entered);(void)k_sem_take(&release_loader,K_MSEC(500));}
}
static int identified(struct xt_bootstrap *out,int64_t end,void *ctx) {
 ARG_UNUSED(end);ARG_UNUSED(ctx);
 strcpy(out->identity.vm_id,"11111111-1111-4111-8111-111111111111");
 strcpy(out->identity.boot_id,"22222222-2222-4222-8222-222222222222");
 strcpy(out->identity.publisher_id,"33333333-3333-4333-8333-333333333333");
 strcpy(out->username,out->identity.vm_id);strcpy(out->password,"native-fixture-only");
 strcpy(out->broker_ipv4,"192.0.2.1");out->port=18883;out->trusted_internal_plaintext=true;return 0;
}
static int load(struct xt_bootstrap *out,int64_t end,void *ctx) {
 ARG_UNUSED(out);ARG_UNUSED(ctx);assert(end>k_uptime_get());
 k_sem_give(&entered);
 int64_t left=end-k_uptime_get();
 if(k_sem_take(&release_loader,K_MSEC(left)))return -ETIMEDOUT;
 return -EACCES;
}
static void stop(void *a,void *b,void *c) {
 ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);
 stop_result=xt_stop(k_uptime_get()+300);
}
int main(void) {
 static const struct xt_inventory info={.os_version="4.4.0",.build_id="lifecycle-test",.profile="network_starter",
  .task_id="diagnostic.exercise",.interface_name="native",.diagnostics=true,.publisher=true};
 assert(!xt_stop(k_uptime_get()+100));
 assert(!xt_start(load,NULL,&info));assert(!k_sem_take(&entered,K_SECONDS(1)));
 struct xt_status pending;assert(!xt_status_get(&pending));assert(pending.running&&!pending.identified&&pending.last_rc==-EAGAIN);
 assert(xt_stop(k_uptime_get()+5)==-ETIMEDOUT);
 assert(xt_start(load,NULL,&info)==-EALREADY); /* Old worker still owns its TCB. */
 k_thread_create(&stopper,stop_stack,K_THREAD_STACK_SIZEOF(stop_stack),stop,NULL,NULL,NULL,7,0,K_NO_WAIT);
 k_sleep(K_MSEC(1));
 assert(xt_start(load,NULL,&info)==-EBUSY); /* stop owns API lock THROUGH join. */
 k_sem_give(&release_loader);
 assert(!k_thread_join(&stopper,K_SECONDS(1)));assert(!stop_result);
 struct xt_status status;assert(!xt_status_get(&status));assert(!status.running&&!status.identified);
 assert(!xt_stop(k_uptime_get()+100));
 assert(!xt_start(load,NULL,&info));assert(!k_sem_take(&entered,K_SECONDS(1)));
 k_sem_give(&release_loader);k_sleep(K_MSEC(1));assert(!xt_stop(k_uptime_get()+100));
 assert(!xt_status_get(&status));assert(!status.running&&!status.connected);
 block_collection=true;assert(!xt_start(identified,NULL,&info));
 assert(!k_sem_take(&entered,K_SECONDS(1)));
 assert(xt_stop(k_uptime_get()+5)==-ETIMEDOUT);k_sem_give(&release_loader);
 assert(!xt_stop(k_uptime_get()+1000));assert(!xt_status_get(&status));
 assert(!status.running&&!status.counters.connection_attempts&&!status.counters.submitted);
 puts("PASS actual Publisher bootstrap/collection stop races, timeout quarantine, join and restart; no post-stop connect/publish");
 exit(0);
}
