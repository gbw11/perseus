/* SPDX-License-Identifier: Apache-2.0 */
/* Production adapter with host kernel/hypercall boundaries and a 2-level event model.
 * Mirrors the pinned Xen set_pending/unmask and Zephyr clear-before-callback order.
 * This models delivery; it does not execute a physical IRQ or emulate the GIC.
 */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include "../src/xenstore_zephyr.c"
#include <zephyr/xen/public/event_channel.h>
static struct xenstore_domain_interface page;
static bool masked[32], pending[32], selector, locked, fail_unmask;
static int irqs, mapped, unmapped, bound, unbound, hypercalls;
static void (*handler)(void *);
static void *handler_context;
static void dispatch(void)
{
	if (!locked && selector) {
		selector=false;
		if (pending[17] && !masked[17] && handler) {
			pending[17]=false;irqs++;handler(handler_context);
		}
	}
}
static void peer_event(void)
{
	page.rsp_prod++;
	if (pending[17]) { return; }
	pending[17]=true;
	if (!masked[17]) { selector=true;dispatch(); }
}
int k_mutex_lock(struct k_mutex *m,int ms) { (void)m;(void)ms;return 0; }
int k_mutex_unlock(struct k_mutex *m) { (void)m;return 0; }
int k_sem_init(struct k_sem *s,unsigned int n,unsigned int limit)
{ (void)limit;s->count=n;return 0; }
void k_sem_give(struct k_sem *s) { s->count=1; }
int k_sem_take(struct k_sem *s,int64_t ms)
{ (void)ms;dispatch();if (!s->count) { return -EAGAIN; }s->count--;return 0; }
int64_t k_uptime_get(void) { return 0; }
int k_is_in_isr(void) { return 0; }
unsigned int irq_lock(void) { locked=true;return 0; }
void irq_unlock(unsigned int key) { (void)key;locked=false;dispatch(); }
int mask_event_channel(uint32_t port) { assert(port==17);masked[port]=true;return 0; }
int unmask_event_channel(uint32_t port) { masked[port]=false;return 0; }
int bind_event_channel(uint32_t port,void (*cb)(void *),void *ctx)
{
	assert(port==17 && masked[port]);handler=cb;handler_context=ctx;bound++;
	peer_event(); /* event arrives while masked, with no other event port involved */
	return 0;
}
int unbind_event_channel(uint32_t port) { assert(port==17);handler=NULL;unbound++;return 0; }
int notify_evtchn(uint32_t port) { assert(port==17);return 0; }
int HYPERVISOR_event_channel_op(int op,void *arg)
{
	struct evtchn_unmask *u=arg;
	assert(op==EVTCHNOP_unmask && u->port==17);hypercalls++;
	assert(masked[17]); /* clearing the shared mask before this call loses replay */
	if (fail_unmask) { return -EIO; }
	bool was_masked=masked[17];masked[17]=false;
	if (was_masked && pending[17] && !selector) { selector=true;dispatch(); }
	return 0;
}
int hvm_get_parameter(int which,int domid,uint64_t *out)
{
	assert(domid==DOMID_SELF);
	uint64_t values[]={0,0x12345,17,18,0x12346};*out=values[which];return 0;
}
void k_mem_map_phys_bare(uint8_t **out,uintptr_t physical,size_t size,int flags)
{
	assert(physical==0x12345000 && size==4096);(void)flags;
	*out=(uint8_t *)&page;mapped++;
}
void k_mem_unmap_phys_bare(void *p,size_t size)
{ assert(p==&page && size==4096);unmapped++; }
static void reset_model(void)
{
	memset(&page,0,sizeof(page));memset(masked,0,sizeof(masked));memset(pending,0,sizeof(pending));
	selector=locked=fail_unmask=false;irqs=mapped=unmapped=bound=unbound=hypercalls=0;
	owner=NULL;session_used=false;handler=NULL;
	masked[18]=true;pending[18]=true; /* existing console state must remain untouched */
}
static void replay_and_second_event(void)
{
	reset_model();struct xs_zephyr z;
	assert(xs_zephyr_open(&z)==0);
	assert(hypercalls==1 && irqs==1 && !pending[17] && !masked[17]);
	assert(z.link.client.io.wait(&z,5)==0 && page.rsp_prod==1);
	peer_event(); /* second response/watch must deliver without a different port */
	assert(irqs==2 && !pending[17]);
	assert(z.link.client.io.wait(&z,5)==0 && page.rsp_prod==2);
	assert(z.link.client.io.wait(&z,5)==-ETIMEDOUT);
	assert(masked[18] && pending[18]);
	page.rsp_cons=page.rsp_prod; /* peer bytes only exercised event delivery */
	assert(xs_zephyr_close(&z,5)==0 && masked[17]);
	assert(mapped==1 && unmapped==1 && bound==1 && unbound==1);
}
static void unmask_failure_cleanup(void)
{
	reset_model();struct xs_zephyr z;fail_unmask=true;
	assert(xs_zephyr_open(&z)==-EIO);
	assert(!owner && !session_used && masked[17]);
	assert(mapped==1 && unmapped==1 && bound==1 && unbound==1);
	assert(masked[18] && pending[18]);
	fail_unmask=false;assert(xs_zephyr_open(&z)==0);
	page.rsp_cons=page.rsp_prod;assert(xs_zephyr_close(&z,5)==0);
}
int main(int argc,char **argv)
{
	if (argc<2 || !strcmp(argv[1],"pending")) { replay_and_second_event();puts("PASS masked pending replay and second event"); }
	if (argc<2 || !strcmp(argv[1],"failure")) { unmask_failure_cleanup();puts("PASS unmask failure cleanup and retry"); }
	return 0;
}
