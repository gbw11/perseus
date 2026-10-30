/* SPDX-License-Identifier: Apache-2.0 */
#ifndef XENOVA_SHUTDOWN_FLOW_H
#define XENOVA_SHUTDOWN_FLOW_H
#include <stdint.h>
enum xsd_phase { XSD_IDLE, XSD_RECEIVED, XSD_QUIESCING, XSD_NET_CLOSING,
 XSD_STORE_CLOSING, XSD_HYPERCALL_RETURNED, XSD_REJECTED, XSD_TIMED_OUT, XSD_FAILED };
struct xsd_ops {
 int64_t (*now)(void *);
 int (*ack)(void *,int64_t);
 int (*quiesce)(void *,const char *,int64_t);
 int (*net_close)(void *,int64_t);
 int (*store_close)(void *,int64_t);
 int (*shutdown)(void *,unsigned);
 void *context;
};
struct xsd_flow { enum xsd_phase phase; int error; struct xsd_ops ops; };
/* ACK is receipt only; a returning shutdown hypercall is always unconfirmed. */
int xsd_run(struct xsd_flow *,const char *request,int64_t deadline);
#endif
