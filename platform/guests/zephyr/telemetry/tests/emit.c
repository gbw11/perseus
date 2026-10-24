/* SPDX-License-Identifier: Apache-2.0 */
/* Production serializer exercised with deterministic source boundary inputs. */
#include <xenova/telemetry_core.h>
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>

static struct xt_publisher publisher;
static struct xt_observation observation;
static struct xt_message message;
static char sample_id[37], snapshot_id[37];

static void emit(enum xt_kind kind,unsigned part)
{
	assert(!xt_sample_id(&publisher,sample_id));
	int rc=xt_encode(&publisher,kind,&observation,50000,sample_id,snapshot_id,part,&message);
	if (rc) { fprintf(stderr,"encode kind=%d part=%u rc=%d\n",kind,part,rc); }
	assert(!rc && message.length<=XT_PAYLOAD_MAX);
	printf("{\"topic\":\"%s\",\"qos\":%u,\"retain\":%s,\"message\":%s}\n",message.topic,message.qos,message.retain ? "true" : "false",message.bytes);
}

int main(void)
{
	struct xt_identity id={.vm_id="f1000000-0000-4000-8000-000000000001",
		.boot_id="f2000000-0000-4000-8000-000000000001",
		.publisher_id="f3000000-0000-4000-8000-000000000001"};
	struct xt_inventory info={.os_version="4.4.0",.build_id="producer-unit-boundaries",
		.profile="diagnostic_exercise",.task_id="arithmetic",.interface_name="eth0",
		.diagnostics=true,.timing=true,.threads=true,.heap=true,.publisher=true,
		.mac="02:11:22:33:44:55",.ipv4="192.0.2.3"};
	assert(!xt_init(&publisher,&id,&info));
	assert(!xt_sample_id(&publisher,snapshot_id));
	observation=(struct xt_observation){.start_ms=49999,.end_ms=50000,.elapsed_us=700,
		.now_us=50000000,.frequency_hz=54000000,.heap_used=9007199254741003ULL,
		.heap_free=2000,.heap_peak=9007199254741103ULL,.threads_rc=-EAGAIN,
		.interface_up=true,.carrier=false,.net={UINT32_MAX,2,3,4,5,6}};
	assert(!xdiag_timing_init(&observation.timing,20000,20000,0));
	assert(!xdiag_timing_begin(&observation.timing,100));
	assert(!xdiag_timing_end(&observation.timing,200));
	assert(!xdiag_timing_begin(&observation.timing,20000));
	assert(!xdiag_timing_end(&observation.timing,20100));
	assert(observation.timing.last_jitter_us==-100);
	for (unsigned i=0;i<20;++i) { assert(!xdiag_history_record(&observation.history,"부팅",i%2 ? -EIO : 0,i,true)); }
	xt_capture_events(&publisher,&observation.history);
	assert(publisher.event_count==12 && publisher.counters.source_missed==8);
	for (enum xt_kind k=XT_INVENTORY;k<=XT_PUBLISHER;++k) { emit(k,0); }
	while (publisher.event_count) { emit(XT_EVENT,0);xt_event_acked(&publisher); }
	observation.rows=32;observation.rows_seen=35;observation.threads_rc=-ENOSPC;
	for (size_t i=0;i<32;++i) {
		observation.threads[i]=(struct xt_thread){.name="제어 작업 \"\\",.state="pending",
			.flags=3,.priority=8,.stack_bytes=8192,.unused_bytes=7000};
		if (i==0) { observation.threads[i].stack_rc=-E2BIG; }
	}
	for (unsigned i=0;i<8;++i) { emit(XT_THREADS,i); }
	emit(XT_HISTORY,0);emit(XT_HISTORY,1);
	observation.net[0]=0;emit(XT_NETWORK,0);
	publisher.counters.submit_failures=UINT64_MAX;emit(XT_PUBLISHER,0);
	observation.heap_rc=-ENOTSUP;observation.network_rc=-EIO;observation.timing_rc=-ENOENT;
	observation.rows=0;observation.rows_seen=0;observation.threads_rc=-EAGAIN;
	emit(XT_MEMORY,0);emit(XT_NETWORK,0);emit(XT_PROGRESS,0);emit(XT_THREADS,0);
	observation.collection_rc=-EBUSY;observation.history_rc=-EBUSY;emit(XT_HEARTBEAT,0);emit(XT_HISTORY,0);
	observation.collection_rc=0;observation.history_rc=0;observation.timing_rc=0;
	memset(&observation.timing.interval,0,sizeof(observation.timing.interval));emit(XT_INTERVAL,0);
	observation.timing.interval=(struct xdiag_metric){.count=1,.min=1ULL<<31,.max=1ULL<<31,.sum=1ULL<<31};
	observation.timing.interval.histogram[31]=1;emit(XT_INTERVAL,0);
	observation.timing.interval.sum_saturated=true;observation.timing.interval.saturated=true;emit(XT_INTERVAL,0);
	uint64_t seq=publisher.sequence[2];
	assert(xt_encode(&publisher,XT_MEMORY,&observation,52001,sample_id,snapshot_id,0,&message)==-ETIMEDOUT);
	assert(publisher.sequence[2]==seq);
	assert(xt_encode(&publisher,XT_THREADS,&observation,50000,sample_id,snapshot_id,8,&message)==-EINVAL);
	/* Invalid UTF-8 never escapes as a partial MQTT message. */
	publisher.inventory.build_id="\xc0\xaf";
	assert(xt_encode(&publisher,XT_INVENTORY,&observation,50000,sample_id,snapshot_id,0,&message)==-EILSEQ);
	assert(message.length==0 && message.bytes[0]==0);
	/* Distinct queue loss, source loss, stable dedup and no allocation. */
	for (unsigned i=20;i<65;++i) {
		assert(!xdiag_history_record(&observation.history,"error",-EIO,i,false));
		xt_capture_events(&publisher,&observation.history);
	}
	assert(publisher.event_count==32 && publisher.counters.events_dropped==13);
	size_t count=publisher.event_count;xt_capture_events(&publisher,&observation.history);assert(count==publisher.event_count);
	fprintf(stderr,"PASS production model, source/queue loss, raw32 wrap, nulls, pages and u64 boundaries\n");
	return 0;
}
