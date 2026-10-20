/* SPDX-License-Identifier: Apache-2.0 */
/* The HVM Store event port and page are borrowed, never closed or freed. */
#include <xenova/xenstore_link.h>
#include <errno.h>
#include <limits.h>
#include <string.h>

static void detach(struct xs_link *link)
{
	if (link->bound) {
		link->ops.mask(link->io.context, link->port);
		link->ops.unbind(link->io.context, link->port);
		link->bound = false;
	}
	if (link->mapping) {
		link->ops.unmap(link->io.context, link->mapping);
		link->mapping = NULL;
	}
	link->open = false;
}

int xs_link_open(struct xs_link *link, const struct xs_link_ops *ops, const struct xs_io *io)
{
	if (!link || !ops || !io || !ops->parameter || !ops->map || !ops->unmap ||
	    !ops->bind || !ops->unbind || !ops->mask || !ops->unmask ||
	    !io->now || !io->wait || !io->notify) { return -EINVAL; }
	memset(link, 0, sizeof(*link));
	link->ops = *ops; link->io = *io;
	uint64_t pfn, port, console_port, console_pfn;
	int rc = ops->parameter(io->context, XS_STORE_PFN, &pfn);
	if (!rc) { rc = ops->parameter(io->context, XS_STORE_PORT, &port); }
	if (!rc) { rc = ops->parameter(io->context, XS_CONSOLE_PORT, &console_port); }
	if (!rc) { rc = ops->parameter(io->context, XS_CONSOLE_PFN, &console_pfn); }
	if (rc) { return rc; }
	if (!pfn || pfn > (UINTPTR_MAX >> 12) || !port || port > UINT32_MAX ||
	    port == console_port || pfn == console_pfn) { return -EINVAL; }
	link->port = (uint32_t)port;
	rc = ops->map(io->context, (uintptr_t)pfn << 12, &link->mapping);
	if (rc) { detach(link); return rc; }
	rc = xs_init(&link->client, link->mapping, io);
	if (rc) { detach(link); return rc; }
	rc = ops->bind(io->context, link->port);
	if (rc) { detach(link); return rc; }
	link->bound = true;
	rc = ops->unmask(io->context, link->port);
	if (rc) { detach(link); return rc; }
	link->open = true;
	return 0;
}

int xs_link_close(struct xs_link *link, int ms)
{
	if (!link || !link->open) { return 0; }
	int rc = xs_close(&link->client, ms);
	if (rc == -EBUSY) { return rc; }
	detach(link);
	return rc;
}
