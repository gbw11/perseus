/* SPDX-License-Identifier: Apache-2.0 */
/** Mapping/event lifetime boundary, shared by Zephyr and host fault tests. */
#ifndef XENOVA_XENSTORE_LINK_H
#define XENOVA_XENSTORE_LINK_H
#include <xenova/xenstore.h>
enum xs_parameter { XS_STORE_PFN, XS_STORE_PORT, XS_CONSOLE_PORT, XS_CONSOLE_PFN };
struct xs_link_ops {
	int (*parameter)(void *, enum xs_parameter, uint64_t *);
	int (*map)(void *, uintptr_t physical, void **virtual_address);
	void (*unmap)(void *, void *);
	int (*bind)(void *, uint32_t);
	void (*unbind)(void *, uint32_t);
	void (*mask)(void *, uint32_t);
	int (*unmask)(void *, uint32_t);
};
struct xs_link {
	struct xs_client client;
	struct xs_link_ops ops;
	struct xs_io io;
	void *mapping;
	uint32_t port;
	bool bound, open;
};
int xs_link_open(struct xs_link *, const struct xs_link_ops *, const struct xs_io *);
int xs_link_close(struct xs_link *, int timeout_ms);
#endif
