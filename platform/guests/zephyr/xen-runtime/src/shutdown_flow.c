/* SPDX-License-Identifier: Apache-2.0 */
#include <xenova/shutdown_flow.h>
#include <errno.h>
#include <string.h>
static int failed(struct xsd_flow *f,int rc) {
 f->error=rc;f->phase=rc==-EBUSY?XSD_REJECTED:rc==-ETIMEDOUT?XSD_TIMED_OUT:XSD_FAILED;
 return rc;
}
static int budget(struct xsd_flow *f,int64_t end) {
 return f->ops.now(f->ops.context)>=end?-ETIMEDOUT:0;
}
int xsd_run(struct xsd_flow *f,const char *request,int64_t end) {
 if(!f||!request)return -EINVAL;
 if(strcmp(request,"poweroff")&&strcmp(request,"reboot"))return -ENOTSUP;
 if(f->phase!=XSD_IDLE)return -EALREADY;
 int rc=budget(f,end);if(rc)return failed(f,rc);
 f->phase=XSD_RECEIVED;
 rc=f->ops.ack(f->ops.context,end);if(rc)return failed(f,rc);
 if((rc=budget(f,end)))return failed(f,rc);
 f->phase=XSD_QUIESCING;
 rc=f->ops.quiesce(f->ops.context,request,end);if(rc)return failed(f,rc);
 if((rc=budget(f,end)))return failed(f,rc);
 f->phase=XSD_NET_CLOSING;
 rc=f->ops.net_close(f->ops.context,end);if(rc)return failed(f,rc);
 if((rc=budget(f,end)))return failed(f,rc);
 f->phase=XSD_STORE_CLOSING;
 rc=f->ops.store_close(f->ops.context,end);if(rc)return failed(f,rc);
 if((rc=budget(f,end)))return failed(f,rc);
 /* Xen ABI SHUTDOWN_poweroff=0 / SHUTDOWN_reboot=1. */
 rc=f->ops.shutdown(f->ops.context,!strcmp(request,"reboot")?1:0);
 f->phase=XSD_HYPERCALL_RETURNED;f->error=rc?rc:-EIO;
 return f->error;
}
