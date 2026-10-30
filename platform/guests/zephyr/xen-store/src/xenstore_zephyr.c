/* SPDX-License-Identifier: Apache-2.0 */
/* Reuse Zephyr's HVM, MMU and event APIs; only the Store port is claimed. */
#include <xenova/xenstore_zephyr.h>
#include <zephyr/xen/events.h>
#include <zephyr/xen/public/event_channel.h>
#include <zephyr/arch/arm64/hypercall.h>
#include <zephyr/xen/hvm.h>
#include <zephyr/xen/generic.h>
#include <zephyr/kernel/mm.h>
#include <zephyr/irq.h>
#include <errno.h>

K_MUTEX_DEFINE(store_owner_lock);
static struct xs_zephyr *owner;
static bool session_used;

static int parameter(void *ctx, enum xs_parameter which, uint64_t *value)
{
	ARG_UNUSED(ctx);
	static const int parameters[] = {
		HVM_PARAM_STORE_PFN, HVM_PARAM_STORE_EVTCHN,
		HVM_PARAM_CONSOLE_EVTCHN, HVM_PARAM_CONSOLE_PFN
	};
	int rc = hvm_get_parameter(parameters[which], DOMID_SELF, value);
	if (!rc && (which == XS_STORE_PFN || which == XS_CONSOLE_PFN) &&
	    *value >= (UINT64_C(1) << (CONFIG_ARM64_PA_BITS - XEN_PAGE_SHIFT))) {
		return -ERANGE;
	}
	return rc;
}

static int map_page(void *ctx, uintptr_t physical, void **out)
{
	ARG_UNUSED(ctx);
	uint8_t *mapping;
	k_mem_map_phys_bare(&mapping, physical, XEN_PAGE_SIZE, K_MEM_CACHE_WB | K_MEM_PERM_RW);
	*out = mapping;
	return mapping ? 0 : -ENOMEM;
}

static void unmap_page(void *ctx, void *address)
{
	ARG_UNUSED(ctx);
	k_mem_unmap_phys_bare(address, XEN_PAGE_SIZE);
}

static void store_event(void *ctx)
{
	struct xs_zephyr *z = ctx;
	k_sem_give(&z->wake);
}

static int bind_port(void *ctx, uint32_t port)
{
	if (port >= EVTCHN_2L_NR_CHANNELS) { return -ERANGE; }
	unsigned int key = irq_lock();
	mask_event_channel(port);
	int rc = bind_event_channel(port, store_event, ctx);
	irq_unlock(key);
	return rc;
}

static void unbind_port(void *ctx, uint32_t port)
{
	ARG_UNUSED(ctx);
	unsigned int key = irq_lock();
	unbind_event_channel(port);
	irq_unlock(key);
}

static void mask_port(void *ctx, uint32_t port)
{
	ARG_UNUSED(ctx);
	mask_event_channel(port);
}

static int unmask_port(void *ctx, uint32_t port)
{
	struct xs_zephyr *z = ctx;
	struct evtchn_unmask request = { .port = port };
	/* Keep the shared mask set until Xen can replay an already-pending event.
	 * Zephyr's shared-bit-only helper would bypass Xen's test-and-clear path.
	 */
	int rc = HYPERVISOR_event_channel_op(EVTCHNOP_unmask, &request);
	if (!rc) { k_sem_give(&z->wake); }
	return rc;
}

static int64_t now(void *ctx)
{
	ARG_UNUSED(ctx);
	return k_uptime_get();
}

static int wait_event(void *ctx, int64_t end)
{
	struct xs_zephyr *z = ctx;
	int64_t left = end - k_uptime_get();
	if (left <= 0) { return -ETIMEDOUT; }
	int rc = k_sem_take(&z->wake, K_MSEC(left));
	return rc == -EAGAIN ? -ETIMEDOUT : rc;
}

static int notify(void *ctx)
{
	return notify_evtchn(((struct xs_zephyr *)ctx)->link.port);
}

int xs_zephyr_open(struct xs_zephyr *z)
{
	if (!z || k_is_in_isr()) { return -EINVAL; }
	k_mutex_lock(&store_owner_lock, K_FOREVER);
	if (owner) { k_mutex_unlock(&store_owner_lock); return -EBUSY; }
	if (session_used) { k_mutex_unlock(&store_owner_lock); return -EALREADY; }
	k_sem_init(&z->wake, 0, 1);
	struct xs_io io = { now, wait_event, notify, z };
	const struct xs_link_ops ops = {
		parameter, map_page, unmap_page, bind_port, unbind_port, mask_port, unmask_port
	};
	int rc = xs_link_open(&z->link, &ops, &io);
	if (!rc) { owner = z; session_used = true; }
	k_mutex_unlock(&store_owner_lock);
	return rc;
}

int xs_zephyr_close(struct xs_zephyr *z, int ms)
{
	if (!z || ms < 0 || k_is_in_isr()) { return -EINVAL; }
	int64_t end = k_uptime_get() + ms;
	if (k_mutex_lock(&store_owner_lock, K_MSEC(ms))) { return -ETIMEDOUT; }
	if (owner != z) { k_mutex_unlock(&store_owner_lock); return -ENOENT; }
	int64_t left = end - k_uptime_get();
	int rc = xs_link_close(&z->link, left > 0 ? left : 0);
	if (!z->link.open) { owner = NULL; }
	k_mutex_unlock(&store_owner_lock);
	return rc;
}
