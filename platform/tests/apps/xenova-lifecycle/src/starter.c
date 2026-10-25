/* Execute the actual Starter main/exercise with the same external boundaries. */
#include <lifecycle_boundary.h>
#include <xenova/netfront.h>
#include <xenova/shutdown.h>
#include <xenova/runtime.h>
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int xl_starter_main(void);
int starter_exercise_stop(int64_t);
int main(void) {
 xl_case=getenv("XL_CASE");assert(xl_case);xl_net_init();
 int rc=xl_starter_main();
 assert(!strcmp(xl_case,"net-ok")?rc==0:rc<0);
 assert(!xshutdown_stop(k_uptime_get()+1000));
 assert(!starter_exercise_stop(k_uptime_get()+1000));
 assert(!xnet_close(k_uptime_get()+1000));
 struct xnet_status n;struct xshutdown_status s;
 assert(!xnet_status_get(&n)&&!xshutdown_status_get(&s));
 assert(n.worker_joined&&!n.grants_held&&n.event_port<0);
 assert(s.worker_joined&&!s.watch_owned&&!s.features_possible);
 assert(!xl_hypercalls); /* Local init errors never become poweroff/reboot. */
 assert(!xsr_close(k_uptime_get()+1000));
 printf("PASS actual Network Starter lifecycle %s initial=%d\n",xl_case,rc);
 exit(0);
}
