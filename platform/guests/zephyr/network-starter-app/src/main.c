/* SPDX-License-Identifier: Apache-2.0 */
/* Development Starter: standard Zephyr network stack, no driving state/output. */
#include <xenova/shutdown.h>
#include <xenova/netfront.h>
#include <xenova/runtime.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>
#include <zephyr/shell/shell.h>
#include <stdlib.h>
#include <string.h>
#include <stdatomic.h>
#include "diagnostics_app.h"
#if defined(CONFIG_XENOVA_TELEMETRY)
#include <xenova/telemetry.h>
int starter_telemetry_start(void);
#endif
#if defined(CONFIG_XENOVA_DIAGNOSTICS)
#include <xenova/diagnostics.h>
#endif
static atomic_int hook_mode;
static atomic_bool stopping;
static int64_t unwind_deadline;
K_MUTEX_DEFINE(starter_lifecycle_lock);
static struct k_spinlock admission_lock;
static bool admit(void) {
 k_spinlock_key_t key=k_spin_lock(&admission_lock);
 bool allowed=!atomic_load(&stopping);
 k_spin_unlock(&admission_lock,key);return allowed;
}
static int quiesce(const char *request,int64_t end,void *context) {
 ARG_UNUSED(context);
 int mode=hook_mode;
 if(mode==1)return -EBUSY;
 if(mode==2)return -EIO;
 if(mode==3){while(k_uptime_get()<end)k_sleep(K_MSEC(10));return -ETIMEDOUT;}
 if(k_uptime_get()>=end)return -ETIMEDOUT;
 k_spinlock_key_t key=k_spin_lock(&admission_lock);
 atomic_store(&stopping,true);
 k_spin_unlock(&admission_lock,key);xnet_stop_request();
 int64_t left=end-k_uptime_get();
 if(left<=0||k_mutex_lock(&starter_lifecycle_lock,K_MSEC(left)))return -ETIMEDOUT;
 k_mutex_unlock(&starter_lifecycle_lock);
 int rc=starter_exercise_stop(end);if(rc)return rc;
#if defined(CONFIG_XENOVA_TELEMETRY)
 rc=xt_stop(end);if(rc)return rc;
#endif
 printk("XENOVA demo quiesce %s: no vehicle output or driving state; hook completed\n",request);
 return 0;
}
static int unwind(int original) {
 if(!unwind_deadline)unwind_deadline=k_uptime_get()+CONFIG_XENOVA_SHUTDOWN_BUDGET_MS;
 int64_t end=unwind_deadline;
 int cleanup=xshutdown_stop(end);
 if(cleanup==-EBUSY)return original; /* External request already owns cleanup. */
 int rc=starter_exercise_stop(end);if(rc&&!cleanup)cleanup=rc;
#if defined(CONFIG_XENOVA_TELEMETRY)
 int publisher=xt_stop(end);if(publisher&&!cleanup)cleanup=publisher;
 if(publisher)rc=publisher;
#endif
 if(!rc){rc=xnet_close(end);if(rc&&!cleanup)cleanup=rc;}
 if(!cleanup)cleanup=xsr_close(end);
 starter_diag_boot("starter-cleanup",cleanup);
 printk("XENOVA Starter initialization rc=%d cleanup=%d; local diagnostics retained\n",original,cleanup);
 return original?original:cleanup;
}
int main(void) {
 k_mutex_lock(&starter_lifecycle_lock,K_FOREVER);
 starter_diag_boot("main-enter",0);
 int rc=xshutdown_start(quiesce,NULL);
 starter_diag_boot("shutdown-handler",rc);
 printk("XENOVA cooperative handler rc=%d\n",rc);
 if(rc||atomic_load(&stopping))goto failed;
 rc=xnet_start();
 starter_diag_network_result(rc);
 printk("XENOVA Ethernet startup rc=%d; DHCP/app readiness independent\n",rc);
 if(rc||atomic_load(&stopping))goto failed;
 if(!admit()){rc=-ECANCELED;goto failed;}
 rc=starter_exercise_start();
 starter_diag_boot("exercise-start",rc); /* Optional exercise is degradable. */
#if defined(CONFIG_XENOVA_TELEMETRY)
 if(admit()) {
  rc=starter_telemetry_start();
  starter_diag_boot("telemetry-start",rc); /* Local diagnostics remain usable. */
 }
#endif
 k_mutex_unlock(&starter_lifecycle_lock);
 return 0;
failed:
 atomic_store(&stopping,true);
 if(!rc)rc=-ECANCELED;
 k_mutex_unlock(&starter_lifecycle_lock);
 return unwind(rc);
}

static int stats_cmd(const struct shell *sh,size_t argc,char **argv) {
 if(argc==2&&!strcmp(argv[1],"waits")) {
  struct xnet_waits w;struct xnet_status s;
  int rc=xnet_waits_get(&w);if(rc)return rc;
  rc=xnet_status_get(&s);if(rc)return rc;
  shell_print(sh,"{\"clock\":\"%s\",\"started\":%s,\"connected\":%s,\"backend_observation\":%d,\"backend_error\":%d,\"observation_error\":%d,\"first_observation_error\":%d,\"backend_wait_stage\":%d,\"packet_polls\":%llu,\"pending_batches\":%llu,\"packet_gap_max_us\":%llu,\"packet_lock_wait_max_us\":%llu,\"packet_lock_hold_max_us\":%llu,\"store_observations\":%llu,\"store_lock_wait_max_us\":%llu,\"store_response_max_us\":%llu,\"store_unavailable\":%llu,\"store_recoveries\":%llu,\"last_backend_success_ms\":%llu}",
   w.cycle_clock?"cycles":"kernel_ticks",s.started?"true":"false",s.connected?"true":"false",s.backend_observation,s.backend_error,s.observation_error,s.first_observation_error,s.backend_wait_stage,
   (unsigned long long)w.packet_polls,(unsigned long long)w.pending_batches,(unsigned long long)w.packet_gap_max_us,
   (unsigned long long)w.packet_lock_wait_max_us,(unsigned long long)w.packet_lock_hold_max_us,
   (unsigned long long)w.store_observations,(unsigned long long)w.store_lock_wait_max_us,(unsigned long long)w.store_response_max_us,
   (unsigned long long)w.store_unavailable,(unsigned long long)w.store_recoveries,(unsigned long long)w.last_backend_success_ms);
  return 0;
 }
 if(argc!=1)return -EINVAL;
 ARG_UNUSED(argc);ARG_UNUSED(argv);struct xnf_stats v;int rc=xnet_stats(&v);if(rc)return rc;
 shell_print(sh,"{\"tx\":%u,\"rx\":%u,\"tx_drop\":%u,\"rx_drop\":%u,\"pool_busy\":%u,\"protocol_errors\":%u}",
             v.tx,v.rx,v.tx_drop,v.rx_drop,v.pool_busy,v.protocol_errors);return 0;
}
static int hook_cmd(const struct shell *sh,size_t argc,char **argv) {
 ARG_UNUSED(sh);if(argc!=2)return -EINVAL;
 if(!strcmp(argv[1],"normal"))hook_mode=0;
 else if(!strcmp(argv[1],"reject"))hook_mode=1;
 else if(!strcmp(argv[1],"fail"))hook_mode=2;
 else if(!strcmp(argv[1],"timeout"))hook_mode=3;
 else return -EINVAL;
 return 0;
}
SHELL_STATIC_SUBCMD_SET_CREATE(xenova_commands,
#if defined(CONFIG_XENOVA_TELEMETRY)
 SHELL_CMD(telemetry,NULL,"Publisher status; threads/history request an explicit snapshot",xt_shell_cmd),
#endif
#if defined(CONFIG_XENOVA_DIAGNOSTICS)
 SHELL_CMD(diag,NULL,"Local snapshot; add threads for intrusive stack analysis",xdiag_shell_cmd),
#endif
#if defined(CONFIG_XENOVA_DIAGNOSTICS_EXERCISE)
 SHELL_CMD(exercise,NULL,"Read common on/off benchmark observer",starter_exercise_cmd),
#endif
 SHELL_CMD(stats,NULL,"Netfront counters; waits for packet/Store observations",stats_cmd),
 SHELL_CMD(hook,NULL,"Development hook: normal/reject/fail/timeout",hook_cmd),
 SHELL_SUBCMD_SET_END);
SHELL_CMD_REGISTER(xenova,&xenova_commands,"Xenova development diagnostics",NULL);
