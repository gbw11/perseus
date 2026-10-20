/* SPDX-License-Identifier: Apache-2.0 */
/** Read-only single-VIF discovery and backend observation. */
#ifndef XENOVA_XENBUS_VIF_H
#define XENOVA_XENBUS_VIF_H
#include <xenova/xenstore.h>
#include <xenova/abi/xenbus.h>

struct xvif {
	uint32_t domid, device, backend_domid;
	enum xenbus_state frontend_state, backend_state;
	uint8_t mac[6];
	char domain[XS_PATH_LIMIT], frontend[XS_PATH_LIMIT], backend[XS_PATH_LIMIT];
};

/* ENODEV: no VIF; EAGAIN: incomplete backend; EACCES: permissions;
 * EPROTO: malformed device; ETIMEDOUT: incomplete backend past the budget.
 */
int xvif_probe(struct xs_client *, struct xvif *, int timeout_ms);
int xvif_discover(struct xs_client *, struct xvif *, int timeout_ms);
int xvif_watch_backend(struct xs_client *, const struct xvif *,
		       uint32_t *watch, int timeout_ms);
/* Rereads both states. EXDEV means the backend path was replaced. */
int xvif_refresh(struct xs_client *, struct xvif *, int timeout_ms);
int xvif_wait_backend(struct xs_client *, struct xvif *, uint32_t watch,
		      enum xenbus_state wanted, int timeout_ms);
#endif
