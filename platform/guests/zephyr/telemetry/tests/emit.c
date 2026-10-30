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
	struct xdr_counter before={.total=100,.busy=10,.at_ms=49000},after={.total=500,.busy=111,.at_ms=50000};
	xdr_window(&before,&after,10000,&observation.resources.cpu);
	assert(!observation.resources.cpu.rc&&observation.resources.cpu.percent_milli==25250);
	observation.resources.sample=1;observation.resources.rows=observation.resources.seen=1;
	observation.resources.raw.at_ms=50000;
	observation.resources.threads[0]=(struct xdr_thread){.lifetime=18446744073709551615ULL,.percent_milli=12500};
	emit(XT_CPU,0);
	observation.resources.regions=1;
	observation.resources.memory[0]=(struct xdr_region){.id="net_rx_data",.used=256,.free=768,.capacity=1024};
	emit(XT_POOLS,0);
	/* A fresh collection must not give an old resource cache a new lease. */
	uint64_t resource_sequence=publisher.sequence[2];
	assert(xt_encode(&publisher,XT_CPU,&observation,52001,sample_id,snapshot_id,0,&message)==-ETIMEDOUT);
	observation.end_ms=51000;
	assert(xt_encode(&publisher,XT_CPU,&observation,52001,sample_id,snapshot_id,0,&message)==-ETIMEDOUT);
	assert(xt_encode(&publisher,XT_POOLS,&observation,52001,sample_id,snapshot_id,0,&message)==-ETIMEDOUT);
	assert(publisher.sequence[2]==resource_sequence);observation.end_ms=50000;
	observation.resources.rows=32;observation.resources.seen=73;observation.resources.threads_rc=-ENOSPC;
	for(unsigned i=0;i<32;i++)observation.resources.threads[i]=(struct xdr_thread){.lifetime=i+1,.percent_milli=i};
	/* All pages use one frozen observation/sample; no collector call here. */
	assert(!xt_sample_id(&publisher,sample_id));
	for(unsigned i=0;i<8;i++){
	 assert(!xt_encode(&publisher,XT_CPU,&observation,50000,sample_id,snapshot_id,i,&message));
	 printf("{\"topic\":\"%s\",\"qos\":0,\"retain\":false,\"message\":%s}\n",message.topic,message.bytes);
	}
	uint32_t fraction;
	assert(!xdr_percent(UINT64_MAX/4,UINT64_MAX,&fraction)&&fraction==25000);
	assert(xdr_percent(2,1,&fraction)==-ERANGE);
	assert(xdr_percent(0,0,&fraction)==-ENODATA);
	xdr_window(NULL,&after,10000,&observation.resources.cpu);assert(observation.resources.cpu.rc==-ENODATA);emit(XT_CPU,0);
	before.at_ms=1;xdr_window(&before,&after,100,&observation.resources.cpu);assert(observation.resources.cpu.rc==-EOVERFLOW);emit(XT_CPU,0);
	/* Discontinuity is the new baseline, never a fabricated zero-usage window. */
	before=after;after=(struct xdr_counter){.total=600,.busy=136,.at_ms=50050};
	xdr_window(&before,&after,100,&observation.resources.cpu);
	assert(!observation.resources.cpu.rc&&observation.resources.cpu.percent_milli==25000);
	before=after;after.at_ms+=50;after.total+=100;after.busy+=10;
	xdr_window(&before,&after,100,&observation.resources.cpu);
	assert(!observation.resources.cpu.rc&&observation.resources.cpu.percent_milli==10000);
	assert(!xt_utc_update(&publisher.utc,49900,50000,1767225600000ULL,25,500,"dom0_adjtimex"));
	char iso[25];uint64_t bound;
	assert(!xt_utc_read(&publisher.utc,50000,iso,&bound));assert(!strcmp(iso,"2026-01-01T00:00:00.000Z")&&bound==126);
	emit(XT_HEARTBEAT,0);
	assert(xt_utc_read(&publisher.utc,80000,iso,&bound)==-ENODATA);
	assert(xt_utc_update(&publisher.utc,50001,50000,1767225600000ULL,25,500,"dom0_adjtimex")==-ERANGE);
	xt_collection_result(&publisher.counters,true,50000);emit(XT_HEARTBEAT,0);
	xt_collection_result(&publisher.counters,false,50000);assert(publisher.counters.collection_failures==1);emit(XT_HEARTBEAT,0);
	publisher.counters.stopping=true;emit(XT_HEARTBEAT,0);publisher.counters.stopping=false;
	assert(!xt_utc_update(&publisher.utc,49900,50000,1767225600000ULL,25,500,"dom0_adjtimex"));
	assert(xt_utc_update(&publisher.utc,50000,50100,1767225700000ULL,25,500,"dom0_adjtimex")==-EAGAIN);
	assert(!publisher.utc.valid);
	assert(!xt_utc_update(&publisher.utc,51000,51100,1767225700000ULL,25,500,"dom0_adjtimex"));
	assert(!xt_utc_read(&publisher.utc,52101,iso,&bound)&&bound==127);
	assert(xt_utc_read(&publisher.utc,81100,iso,&bound)==-ENODATA);
	assert(!xt_utc_update(&publisher.utc,82000,82100,1767225731000ULL,25,500,"dom0_adjtimex"));
	assert(!xt_utc_read(&publisher.utc,82100,iso,&bound)&&bound==126);
	assert(xt_utc_update(&publisher.utc,1,5002,1767225600000ULL,25,500,"dom0_adjtimex")==-ERANGE);
	assert(!publisher.utc.valid);
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
