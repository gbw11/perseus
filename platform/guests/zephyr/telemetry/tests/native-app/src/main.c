/* SPDX-License-Identifier: Apache-2.0 */
/* Actual Core/Publisher/MQTT over native TAP. Runtime-only test credentials;
 * deterministic timing boundaries are tested separately in the portable test. */
#include <xenova/telemetry.h>
#include <xenova/diagnostics.h>
#include <zephyr/kernel.h>
#include <zephyr/version.h>
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <inttypes.h>
#include <zephyr/net/net_pkt.h>
#include <zephyr/net/net_if.h>

static struct xdiag_loop loop;
static char instance_id[37];
static struct xt_inventory info={.os_version=KERNEL_VERSION_STRING,.build_id="native-actual-publisher",
	.profile="diagnostic_exercise",.task_id="diagnostic.exercise",.interface_name="native-tap",
	.diagnostics=true,.timing=true,.threads=true,.heap=true,.publisher=true};

/* TAP has no Xen ring. Supply deterministic bytes at the netfront boundary. */
static int test_network(struct xdiag_network *out, void *context)
{
	ARG_UNUSED(context);
	uint64_t age=k_uptime_get();
	uint64_t epoch=age>=4500 ? 2 : 1;
	uint64_t elapsed=epoch==2 ? age-4500 : age;
	*out=(struct xdiag_network){.rx_bytes=elapsed*8,.tx_bytes=elapsed*4,.counter_epoch=epoch};
	return 0;
}

static int bootstrap_load(struct xt_bootstrap *out,int64_t deadline,void *context)
{
	ARG_UNUSED(context);
	if (k_uptime_get()>=deadline) { return -ETIMEDOUT; }
	const char *path=getenv("XT_TEST_BOOTSTRAP");if (!path) { return -ENOENT; }
	FILE *file=fopen(path,"rb");if (!file) { return -errno; }
	char json[2049]={0};size_t length=fread(json,1,sizeof(json)-1,file);bool overflow=fgetc(file)!=EOF;fclose(file);
	int rc=overflow ? -EOVERFLOW : xt_bootstrap_decode(json,length,instance_id,out);
	memset(json,0,sizeof(json));return rc;
}

void xt_test_observation(const struct xdiag_snapshot *raw,const struct xt_observation *s)
{
	const struct xdr_snapshot *r=&raw->resources;
	printf("XT_SOURCE {\"start_ms\":\"%" PRIu64 "\",\"end_ms\":\"%" PRIu64
		"\",\"heap_used\":\"%" PRIu64 "\",\"heap_free\":\"%" PRIu64
		"\",\"completed\":\"%" PRIu64 "\",\"in_flight\":%s,\"jitter\":\"%" PRId64
		"\",\"timing_rc\":%d,\"rx_bytes\":\"%" PRIu64 "\",\"tx_bytes\":\"%" PRIu64
		"\",\"rx_bytes_per_s\":\"%" PRIu64 "\",\"net_uptime_ms\":\"%" PRIu64
		"\",\"net_epoch\":\"%" PRIu64 "\"}\n",
		s->start_ms,s->end_ms,(uint64_t)raw->heap_allocated_bytes,(uint64_t)raw->heap_free_bytes,
		raw->timing.loop_seq,raw->timing.in_flight ? "true" : "false",raw->timing.last_jitter_us,
		raw->timing_rc,r->rx_bytes,r->tx_bytes,r->rx_bytes_per_s,r->net_end_ms,r->net_epoch);
	printf("XT_RESOURCE {\"sample\":\"%llu\",\"rc\":%d,\"total\":\"%llu\",\"busy\":\"%llu\",\"total_delta\":\"%llu\",\"busy_delta\":\"%llu\"}\n",
	 r->sample,r->cpu.rc,r->raw.total,r->raw.busy,r->cpu.total_delta,r->cpu.busy_delta);
}

static int64_t option(const char *name,int64_t fallback)
{
	const char *s=getenv(name);return s ? strtoll(s,NULL,10) : fallback;
}

int main(void)
{
	const char *own=getenv("XT_TEST_INSTANCE_ID");assert(own && xt_uuid_valid(own));strcpy(instance_id,own);
	if (getenv("XT_TEST_BOOTSTRAP_CHECK")) {
		/* Exercise the product decoder without starting Publisher or changing Store. */
		FILE *file=fopen(getenv("XT_TEST_BOOTSTRAP"),"rb");assert(file);
		char json[2049]={0};size_t bytes=fread(json,1,sizeof(json)-1,file);
		bool overflow=fgetc(file)!=EOF;fclose(file);struct xt_bootstrap value;
		int rc=overflow ? -EOVERFLOW : xt_bootstrap_decode_channel(json,bytes,own,
			getenv("XT_TEST_ACCOUNT_CHANNEL"),&value);
		memset(json,0,sizeof(json));memset(&value,0,sizeof(value));
		printf("BOOTSTRAP_CHECK %d\n",rc);exit(rc ? 1 : 0);
	}
	assert(!xdiag_boot("native-main",0));
	assert(!xdiag_loop_init(&loop,20000,20000));assert(!xdiag_loop_watch(&loop));
	assert(!xdiag_network_register(test_network,NULL));
	assert(!xt_start(bootstrap_load,NULL,&info));
	int64_t start=k_uptime_get();bool stalled=false,requested=false,restarted=false,history_requested=false;
	int64_t duration=option("XT_TEST_DURATION_MS",25000),stall=option("XT_TEST_STALL_MS",8000);
	int64_t restart=option("XT_TEST_RESTART_MS",0);
	int64_t next_report=0,next_error=0;char reported_publisher[37]={0};
	void *allocation=k_malloc(1024);assert(allocation);
	struct net_pkt *held=net_pkt_rx_alloc_with_buffer(net_if_get_default(),512,NET_AF_UNSPEC,0,K_NO_WAIT);assert(held);
	while (k_uptime_get()-start<duration) {
		int64_t age=k_uptime_get()-start;
		if(getenv("XT_TEST_FATAL_MS")&&age>=option("XT_TEST_FATAL_MS",3000)){k_oops();}
		if (age>=next_report) {
			struct xt_status state;assert(!xt_status_get(&state));
			if (state.identified && strcmp(reported_publisher,state.identity.publisher_id)) {
				printf("XT_SESSION {\"vm_id\":\"%s\",\"boot_id\":\"%s\",\"publisher_instance_id\":\"%s\"}\n",state.identity.vm_id,state.identity.boot_id,state.identity.publisher_id);
				strcpy(reported_publisher,state.identity.publisher_id);fflush(stdout);
			}
			next_report=age+100;
		}
		if (getenv("XT_TEST_ERROR_BURST") && age>=11000 && age<15000 && age>=next_error) {
			assert(!xdiag_error("native-source-error",-EIO));next_error=age+20;
		}
		if (!requested && age>=4000) { int rc=xt_snapshot_request(true);if (!rc) { requested=true; } }
		if (!history_requested && age>=16500) { int rc=xt_snapshot_request(false);if (!rc) { history_requested=true; } }
		if (restart && !restarted && age>=restart) {
			assert(!xt_stop(k_uptime_get()+1000));assert(!xt_start(bootstrap_load,NULL,&info));restarted=true;
		}
		uint64_t now;assert(!xdiag_clock_us(&now));
		if (!stalled && now>=loop.timing.next_release_us) {
			assert(!xdiag_loop_begin(&loop));
			if (age>=stall) { stalled=true;k_free(allocation);allocation=NULL;net_pkt_unref(held);held=NULL; }
			else {
				volatile unsigned result=0;for (unsigned i=0;i<128;++i) { result=result*1664525U+i; }(void)result;
				assert(!xdiag_loop_end(&loop));
			}
		}
		k_sleep(K_MSEC(1));
	}
	uint64_t before;assert(!xdiag_clock_us(&before));
	assert(!xt_stop(k_uptime_get()+1000));
	uint64_t after;assert(!xdiag_clock_us(&after));
	struct xt_status state;assert(!xt_status_get(&state));
	struct xdr_snapshot resources;assert(!xdiag_resources_get(&resources));
	printf("XT_COST {\"resource_samples\":\"%" PRIu64 "\",\"resource_total_us\":\"%" PRIu64 "\",\"resource_max_us\":\"%" PRIu64 "\",\"publisher_collect_us\":\"%" PRIu64 "\",\"publisher_serialize_us\":\"%" PRIu64 "\",\"publisher_send_us\":\"%" PRIu64 "\"}\n",
	 resources.sample,resources.total_collect_us,resources.max_collect_us,state.counters.collect_us,state.counters.serialized_us,state.counters.send_us);
	printf("XT_STOP {\"elapsed_us\":\"%" PRIu64 "\",\"submitted\":\"%" PRIu64 "\",\"pubacked\":\"%" PRIu64 "\",\"live_discarded\":\"%" PRIu64 "\",\"puback_timeouts\":\"%" PRIu64 "\",\"worker_running\":%s}\n",after-before,state.counters.submitted,state.counters.pubacked,state.counters.live_discarded,state.counters.puback_timeouts,state.running ? "true" : "false");
	if (allocation) { k_free(allocation); }
	if(held)net_pkt_unref(held);
	printf("PASS native actual Publisher stopped; reporter did not complete stalled work\n");
	exit(0);
}
