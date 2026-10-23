/* SPDX-License-Identifier: Apache-2.0 */
/* Development Starter: standard Zephyr network stack, no driving state/output. */
#include <xenova/shutdown.h>
#include <xenova/netfront.h>
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
static int quiesce(const char *request,int64_t end,void *context) {
 ARG_UNUSED(context);
 int mode=hook_mode;
 if(mode==1)return -EBUSY;
 if(mode==2)return -EIO;
 if(mode==3){while(k_uptime_get()<end)k_sleep(K_MSEC(10));return -ETIMEDOUT;}
 if(k_uptime_get()>=end)return -ETIMEDOUT;
 int rc=starter_exercise_stop(end);if(rc)return rc;
#if defined(CONFIG_XENOVA_TELEMETRY)
 rc=xt_stop(end);if(rc)return rc;
#endif
 printk("XENOVA demo quiesce %s: no vehicle output or driving state; hook completed\n",request);
 return 0;
}
int main(void) {
 starter_diag_boot("main-enter",0);
 int rc=xshutdown_start(quiesce,NULL);
 starter_diag_boot("shutdown-handler",rc);
 printk("XENOVA cooperative handler rc=%d\n",rc);
 rc=xnet_start();
 starter_diag_network_result(rc);
 printk("XENOVA Ethernet startup rc=%d; DHCP/app readiness independent\n",rc);
 starter_exercise_start();
#if defined(CONFIG_XENOVA_TELEMETRY)
 rc=starter_telemetry_start();
 starter_diag_boot("telemetry-start",rc);
#endif
 return 0;
}

static int stats_cmd(const struct shell *sh,size_t argc,char **argv) {
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
 SHELL_CMD(stats,NULL,"Read bounded netfront counters",stats_cmd),
 SHELL_CMD(hook,NULL,"Development hook: normal/reject/fail/timeout",hook_cmd),
 SHELL_SUBCMD_SET_END);
SHELL_CMD_REGISTER(xenova,&xenova_commands,"Xenova development diagnostics",NULL);
