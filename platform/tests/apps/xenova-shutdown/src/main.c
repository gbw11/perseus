/* SPDX-License-Identifier: Apache-2.0 */
/* Target API smoke only; no network driver or driving behavior. */
#include <xenova/shutdown.h>
#include <zephyr/kernel.h>
static int quiesce(const char *request,int64_t deadline,void *context) {
 ARG_UNUSED(request);ARG_UNUSED(deadline);ARG_UNUSED(context);return 0;
}
int main(void){return xshutdown_start(quiesce,NULL);}
