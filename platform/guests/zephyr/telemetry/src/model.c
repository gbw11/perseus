/* SPDX-License-Identifier: Apache-2.0 */
/* Diagnostics adapter and closed v2 serializer. No transport or app-health claim. */
#include <xenova/telemetry_core.h>
#include "writer.h"
#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

static const char *const streams[] = {"inventory","heartbeat","metrics","events","snapshot"};
static const char *boolean(bool v) { return v ? "true" : "false"; }

static bool valid_event(const struct xdiag_event *e)
{
	if (!e->event_seq || e->code>0 || !e->stage[0] || strlen(e->stage)>31 || !xt_utf8_valid(e->stage)) { return false; }
	for (const unsigned char *s=(const unsigned char *)e->stage;*s;++s) {
		if (*s<32 || *s==127 || (*s==0xc2 && s[1]>=0x80 && s[1]<=0x9f)) { return false; }
	}
	return true;
}

bool xt_uuid_valid(const char *s)
{
	if (!s || strlen(s)!=36) { return false; }
	bool nonzero=false;
	for (unsigned i=0;i<36;++i) {
		if (i==8 || i==13 || i==18 || i==23) { if (s[i]!='-') { return false; } }
		else if (!((s[i]>='0' && s[i]<='9') || (s[i]>='a' && s[i]<='f'))) { return false; }
		else if (s[i]!='0') { nonzero=true; }
	}
	return nonzero;
}

int xt_init(struct xt_publisher *p, const struct xt_identity *id, const struct xt_inventory *info)
{
	if (!p || !id || !info || !xt_uuid_valid(id->vm_id) || !xt_uuid_valid(id->boot_id) ||
	    !xt_uuid_valid(id->publisher_id)) { return -EINVAL; }
	bool bound=id->host_boot_id[0] || id->execution_id[0] || id->receipt_id[0];
	if (bound && (!xt_uuid_valid(id->host_boot_id) || !xt_uuid_valid(id->execution_id) ||
		      !xt_uuid_valid(id->receipt_id))) { return -EINVAL; }
	if (!info->os_version || !info->build_id || !info->task_id || !info->interface_name ||
	    !info->profile || strlen(info->task_id)>63 || strlen(info->interface_name)>31 ||
	    strlen(info->build_id)>128 || strlen(info->os_version)>64 ||
	    (strcmp(info->profile,"network_starter") && strcmp(info->profile,"diagnostic_exercise") &&
	     strcmp(info->profile,"driving_app") && strcmp(info->profile,"linux_agent"))) { return -EINVAL; }
	memset(p,0,sizeof(*p)); p->identity=*id; p->inventory=*info;
	return 0;
}

static int uuid_offset(const char *seed,uint64_t offset,char out[37])
{
	uint64_t carry=offset;
	unsigned char value[16]; unsigned n=0;
	for (unsigned i=0;i<36;) {
		if (seed[i]=='-') { ++i; continue; }
		const char *s=seed+i;
		unsigned high=s[0]<='9' ? s[0]-'0' : s[0]-'a'+10;
		unsigned low=s[1]<='9' ? s[1]-'0' : s[1]-'a'+10;
		unsigned v=high*16+low;
		value[n++]=(unsigned char)v; i+=2;
	}
	for (int i=15;i>=0;--i) { unsigned sum=value[i]+(carry & 255); value[i]=(unsigned char)sum; carry=(carry>>8)+(sum>>8); }
	if (carry) { return -ERANGE; }
	n=0;
	for (unsigned i=0;i<16;++i) {
		if (i==4 || i==6 || i==8 || i==10) { out[n++]='-'; }
		(void)snprintf(out+n,3,"%02x",value[i]); n+=2;
	}
	return 0;
}

int xt_sample_id(struct xt_publisher *p,char out[37])
{
	if (p->sample_sequence==UINT64_MAX) { return -ERANGE; }
	return uuid_offset(p->identity.publisher_id,++p->sample_sequence,out);
}

static size_t history_events(const struct xdiag_history *h, struct xdiag_event out[16])
{
	size_t n=0;
	for (size_t i=0;i<h->boot_count && i<XDIAG_RECORDS;++i) { out[n++]=h->boot[i]; }
	for (size_t i=0;i<h->error_entries && i<XDIAG_RECORDS;++i) {
		bool duplicate=false;
		for (size_t j=0;j<n;++j) { if (out[j].event_seq==h->errors[i].event_seq) { duplicate=true; } }
		if (!duplicate) { out[n++]=h->errors[i]; }
	}
	for (size_t i=1;i<n;++i) { struct xdiag_event e=out[i]; size_t j=i;
		while (j && out[j-1].event_seq>e.event_seq) { out[j]=out[j-1]; --j; } out[j]=e; }
	return n;
}

void xt_capture_events(struct xt_publisher *p, const struct xdiag_history *h)
{
	struct xdiag_event events[16]; size_t n=history_events(h,events);
	for (size_t i=0;i<n;++i) {
		if (events[i].event_seq<=p->event_cursor) { continue; }
		p->counters.source_missed+=events[i].event_seq-p->event_cursor-1;
		p->event_cursor=events[i].event_seq;
		if (!valid_event(&events[i])) { ++p->counters.events_dropped;continue; }
		if (p->event_count==XT_EVENT_CAPACITY) {
			memmove(p->events,p->events+1,(XT_EVENT_CAPACITY-1)*sizeof(p->events[0]));
			--p->event_count; ++p->counters.events_dropped;
		}
		p->events[p->event_count++]=events[i];
	}
}
void xt_event_acked(struct xt_publisher *p)
{
	if (p->event_count) { --p->event_count; memmove(p->events,p->events+1,p->event_count*sizeof(p->events[0])); }
}

static void collection(struct xt_writer *w, const struct xt_observation *s, const char *id)
{
	xt_raw(w,"\"collection\":{\"sample_id\":"); xt_string(w,id);
	xt_raw(w,",\"start_uptime_ms\":"); xt_u64(w,s->start_ms);
	xt_raw(w,",\"end_uptime_ms\":"); xt_u64(w,s->end_ms);
	xt_raw(w,",\"elapsed_us\":"); if (s->elapsed_rc) { xt_raw(w,"null"); } else { xt_u64(w,s->elapsed_us); }
	if (s->collection_rc>0) { w->error=-ERANGE; }
	xt_raw(w,",\"status\":\"%s\",\"source_rc\":%d}",s->collection_rc ? "collection_failed" : "ok",s->collection_rc);
}

static const char *status(int rc)
{
	if (!rc) { return "ok"; }
	if (rc==-ENOTSUP) { return "unsupported"; }
	if (rc==-ENOENT) { return "not_registered"; }
	if (rc==-ENODATA) { return "no_sample"; }
	return "collection_failed";
}
static const char *cpu_status(int rc){return rc==-EOVERFLOW?"counter_discontinuity":status(rc);}

static void sample(struct xt_writer *w, struct xt_publisher *p, const char *name,
	const char *unit, const char *kind, unsigned encoding, int rc, uint64_t value,
	unsigned labels, const char *override)
{
	const char *enc[]={"u64_decimal","i64_decimal","boolean","float64"};
	const char *state=override ? override : status(rc);
	if (rc>0) { w->error=-ERANGE; }
	xt_raw(w,"{\"name\":"); xt_string(w,name);
	xt_raw(w,",\"unit\":\"%s\",\"kind\":\"%s\",\"encoding\":\"%s\",\"value\":",unit,kind,enc[encoding]);
	if (strcmp(state,"ok")) { xt_raw(w,"null"); }
	else if (encoding==2) { xt_raw(w,"%s",boolean(value!=0)); }
	else if (encoding==1) { xt_i64(w,(int64_t)value); }
	else if (encoding==3) {
		/* Exact thousandths, locale/libc float formatting independent. */
		if(value>100000){w->error=-ERANGE;return;}
		xt_raw(w,"%u.%03u",(unsigned)(value/1000),(unsigned)(value%1000));
	} else { xt_u64(w,value); }
	xt_raw(w,",\"status\":\"%s\",\"reason\":",state);
	xt_string(w,strcmp(state,"ok") ? state : NULL);
	xt_raw(w,",\"source_rc\":%d,\"labels\":{",rc);
	if (labels==1) { xt_raw(w,"\"interface\":"); xt_string(w,p->inventory.interface_name); }
	if (labels==2) {
		xt_raw(w,"\"task_id\":"); xt_string(w,p->inventory.task_id);
		xt_raw(w,",\"task_epoch\":"); xt_string(w,p->task_epoch);
	}
	xt_raw(w,"}}");
}

static void capability(struct xt_writer *w,const char *id,bool supported,const char *reason)
{
	xt_raw(w,"{\"id\":");xt_string(w,id);
	xt_raw(w,",\"support\":\"%s\",\"reason\":",supported ? "available" : "unsupported");
	xt_string(w,supported ? NULL : reason);xt_raw(w,"}");
}

static void inventory(struct xt_writer *w, struct xt_publisher *p,const struct xt_observation *s)
{
	const struct xt_inventory *i=&p->inventory;
	xt_raw(w,",\"os\":{\"id\":\"zephyr\",\"version\":");xt_string(w,i->os_version);
	xt_raw(w,",\"kernel_release\":");xt_string(w,i->os_version);
	xt_raw(w,"},\"hostname\":");xt_string(w,i->hostname[0]?i->hostname:NULL);
	xt_raw(w,",\"display_name\":");xt_string(w,i->display_name[0]?i->display_name:NULL);
	xt_raw(w,",\"firmware\":{\"build_id\":");xt_string(w,i->build_id);
	xt_raw(w,",\"artifact_sha256\":");xt_string(w,i->artifact_sha256[0]?i->artifact_sha256:NULL);
	xt_raw(w,",\"measurement_profile\":");xt_string(w,i->profile);
	xt_raw(w,"},\"publisher\":{\"name\":\"xenova.telemetry\",\"version\":\"2.1.0\",\"metric_catalog\":\"xenova-telemetry-core-2.1\"},\"interfaces\":[");
	if (i->mac[0]) {
		xt_raw(w,"{\"name\":");xt_string(w,i->interface_name);xt_raw(w,",\"mac\":");xt_string(w,i->mac);
		xt_raw(w,",\"addresses\":[");
		if (i->ipv4[0]) { xt_raw(w,"{\"family\":\"ipv4\",\"address\":");xt_string(w,i->ipv4);xt_raw(w,"}"); }
		xt_raw(w,"]}");
	}
	xt_raw(w,"],\"interfaces_omitted\":0,\"capabilities\":[");
	capability(w,"kernel_heap",i->heap,"not enabled in this build");xt_raw(w,",");
	capability(w,"thread_stack",i->threads,"not enabled in this build");xt_raw(w,",");
	capability(w,"timing",i->timing,"64-bit timing unavailable");xt_raw(w,",");
	capability(w,"cpu_runtime",i->cpu_runtime,"runtime provider not enabled");xt_raw(w,",");
	capability(w,"memory_pools",i->pools,"fixed data pool statistics not enabled");xt_raw(w,",");
	capability(w,"persistent_crash",i->persistent_crash,"no verified external capture configuration");
	xt_raw(w,",{\"id\":\"loop\",\"support\":\"%s\",\"reason\":",s->timing_rc ? "not_registered" : "available");
	xt_string(w,s->timing_rc ? "no observed task registered" : NULL);
	xt_raw(w,"}],\"capabilities_omitted\":0,\"clock_domains\":{\"uptime\":\"guest_boot_milliseconds\",\"loop_counter\":\"local_hw_cycles_converted_us\",\"frequency_hz\":");
	if (s->frequency_hz) { xt_u64(w,s->frequency_hz); } else { xt_raw(w,"null"); }
	xt_raw(w,",\"same_epoch\":false},\"limits\":{\"max_payload_bytes\":4096,\"heartbeat_period_ms\":2000,\"metrics_period_ms\":2000,\"event_queue_capacity\":32,\"diagnostic_stack_scan_default\":\"off\"}");
}

static void heartbeat(struct xt_writer *w,const struct xt_counters *c)
{
	xt_raw(w,",\"publisher_state\":\"%s\",\"last_collection_success_uptime_ms\":",c->stopping ? "stopping" : c->collection_degraded ? "degraded" : "running");
	if (c->has_success) { xt_u64(w,c->last_success_ms); } else { xt_raw(w,"null"); }
	xt_raw(w,",\"collection_failures_total\":");xt_u64(w,c->collection_failures);
	xt_raw(w,",\"connection_attempts_total\":");xt_u64(w,c->connection_attempts);
	xt_raw(w,",\"publish_submit_failures_total\":");xt_u64(w,c->submit_failures);
	xt_raw(w,",\"events_dropped_total\":");xt_u64(w,c->events_dropped);
	xt_raw(w,",\"app_health_asserted\":false");
}

static void metrics(struct xt_writer *w,struct xt_publisher *p,enum xt_kind kind,const struct xt_observation *s)
{
	const char *family=kind==XT_MEMORY ? "memory" : kind==XT_NETWORK ? "network" : kind==XT_PROGRESS ? "loop_progress" : "publisher";
	xt_raw(w,",\"family\":\"%s\",\"samples\":[",family);
	if (kind==XT_MEMORY) {
		sample(w,p,"memory.kernel_heap.used_bytes","bytes","gauge",0,s->heap_rc,s->heap_used,0,NULL);xt_raw(w,",");
		sample(w,p,"memory.kernel_heap.free_bytes","bytes","gauge",0,s->heap_rc,s->heap_free,0,NULL);xt_raw(w,",");
		sample(w,p,"memory.kernel_heap.high_water_bytes","bytes","gauge",0,s->heap_rc,s->heap_peak,0,NULL);
	} else if (kind==XT_NETWORK) {
		const char *names[]={"tx_packets","rx_packets","tx_drop","rx_drop","pool_busy","protocol_errors"};
		for (unsigned i=0;i<6;++i) {
			char name[64];(void)snprintf(name,sizeof(name),"network.netfront.%s",names[i]);
			int rc=s->network_rc; if (!rc && s->net[i]>UINT32_MAX) { rc=-ERANGE; }
			sample(w,p,name,i<2 ? "packets" : "count","raw_counter",0,rc,s->net[i],1,NULL);xt_raw(w,",");
		}
		sample(w,p,"network.interface_up","bool","gauge",2,s->network_rc,s->interface_up,1,NULL);xt_raw(w,",");
		sample(w,p,"network.carrier","bool","gauge",2,s->network_rc,s->carrier,1,NULL);
	} else if (kind==XT_PROGRESS) {
		const struct xdiag_timing *t=&s->timing;
		const char *names[]={"completed_total","in_flight","last_completed_age_us","deadline_miss_total","skipped_release_total","last_interval_us","last_jitter_us"};
		uint64_t age=s->now_us>=t->last_completion_us ? s->now_us-t->last_completion_us : 0;
		uint64_t values[]={t->loop_seq,t->in_flight,age,t->deadline_miss_count,t->skipped_release_count,t->interval.last,(uint64_t)t->last_jitter_us};
		for (unsigned i=0;i<7;++i) {
			int rc=s->timing_rc;
			if (!rc && ((i==2 && !t->loop_seq) || (i>=5 && !t->interval.count))) { rc=-ENODATA; }
			if (!rc && i==2 && s->now_us<t->last_completion_us) { rc=-EIO; }
			char name[64];(void)snprintf(name,sizeof(name),"loop.%s",names[i]);
			sample(w,p,name,i==1 ? "bool" : (i==2 || i>=5) ? "us" : "count",(i==0 || i==3 || i==4) ? "counter" : "gauge",i==1 ? 2 : i==6 ? 1 : 0,rc,values[i],2,(!rc && t->saturated && (i==0 || i==3 || i==4)) ? "saturated" : NULL);
			if (i<6) { xt_raw(w,","); }
		}
	} else {
		const char *names[]={"collection_failures_total","publish_submit_failures_total","events_dropped_total","payload_oversize_total","puback_timeouts_total"};
		const struct xt_counters *c=&p->counters;
		uint64_t values[]={c->collection_failures,c->submit_failures,c->events_dropped,c->payload_oversize,c->puback_timeouts};
		for (unsigned i=0;i<5;++i) {
			char name[64];(void)snprintf(name,sizeof(name),"publisher.%s",names[i]);
			sample(w,p,name,"count","counter",0,0,values[i],0,NULL);if (i<4) { xt_raw(w,","); }
		}
	}
	xt_raw(w,"]");
}

static void distribution(struct xt_writer *w,struct xt_publisher *p,enum xt_kind kind,const struct xt_observation *s)
{
	const struct xdiag_metric *metrics[]={&s->timing.interval,&s->timing.absolute_jitter,&s->timing.lateness,&s->timing.elapsed};
	const char *names[]={"interval","absolute_jitter","schedule_lateness","step_elapsed"};
	unsigned index=(unsigned)(kind-XT_INTERVAL); const struct xdiag_metric *m=metrics[index];
	xt_raw(w,",\"family\":\"loop_timing\",\"distribution\":{\"name\":\"%s\",\"task_id\":",names[index]);xt_string(w,p->inventory.task_id);
	xt_raw(w,",\"task_epoch\":");xt_string(w,p->task_epoch);
	xt_raw(w,",\"unit\":\"us\",\"scope\":\"task_epoch_cumulative\",\"status\":\"%s\",\"count\":",!m->count ? "no_sample" : m->saturated ? "saturated" : "ok");xt_u64(w,m->count);
	const char *fields[]={"min_us","max_us","sum_us"};uint64_t values[]={m->min,m->max,m->sum};
	for (unsigned i=0;i<3;++i) { xt_raw(w,",\"%s\":",fields[i]);if (!m->count || (i==2 && m->sum_saturated)) { xt_raw(w,"null"); } else { xt_u64(w,values[i]); } }
	xt_raw(w,",\"count_saturated\":%s,\"sum_saturated\":%s,\"histogram_saturated\":%s,\"histogram_kind\":\"xdiag_log2_32_v1\",\"buckets\":[",boolean(m->count_saturated),boolean(m->sum_saturated),boolean(m->histogram_saturated));
	for (unsigned i=0;i<32;++i) { if (i) { xt_raw(w,","); }xt_u64(w,m->histogram[i]); }
	xt_raw(w,"]");
	for (unsigned q=95;q<=99;q+=4) { uint64_t upper;xt_raw(w,",\"p%u_upper_us\":",q);if (xdiag_percentile_upper(m,q,&upper)) { xt_raw(w,"null"); } else { xt_u64(w,upper); } }
	xt_raw(w,"}");
}

static void resources(struct xt_writer *w,struct xt_publisher *p,enum xt_kind kind,
 const struct xt_observation *s,unsigned part)
{
 const struct xdr_snapshot *r=&s->resources;
 if(r->rows>XDR_THREADS||r->regions>XDR_REGIONS){w->error=-ERANGE;return;}
 if(kind==XT_POOLS){
  xt_raw(w,",\"family\":\"memory_regions\",\"source_sample_seq\":");xt_u64(w,r->sample);
  xt_raw(w,",\"observed_uptime_ms\":");xt_u64(w,r->raw.at_ms);xt_raw(w,",\"samples\":[");
  for(size_t i=0;i<r->regions;i++){
   const struct xdr_region *m=&r->memory[i];
   if(m->rc>0){w->error=-ERANGE;return;}
   if(i)xt_raw(w,",");
   const char *names[]={"used_bytes","free_bytes","capacity_bytes"};
   uint64_t values[]={m->used,m->free,m->capacity};
   for(unsigned j=0;j<3;j++){
    if(j)xt_raw(w,",");
    xt_raw(w,"{\"name\":\"memory.region.%s\",\"unit\":\"bytes\",\"kind\":\"gauge\",\"encoding\":\"u64_decimal\",\"value\":",names[j]);
    if(m->rc)xt_raw(w,"null");else xt_u64(w,values[j]);
    xt_raw(w,",\"status\":\"%s\",\"reason\":",status(m->rc));xt_string(w,m->rc?status(m->rc):NULL);
    xt_raw(w,",\"source_rc\":%d,\"labels\":{\"region\":",m->rc);xt_string(w,m->id);xt_raw(w,"}}");
   }
  }
  xt_raw(w,"]");return;
 }
 unsigned count=(unsigned)((r->rows+3)/4);if(!count)count=1;
 if(part>=count){w->error=-EINVAL;return;}
 xt_raw(w,",\"family\":\"cpu\",\"window\":{\"sample_seq\":");xt_u64(w,r->sample);
 xt_raw(w,",\"start_uptime_ms\":");xt_u64(w,r->cpu.start_ms);
 xt_raw(w,",\"end_uptime_ms\":");xt_u64(w,r->cpu.end_ms);
 xt_raw(w,",\"scope\":\"guest_accounted_up\",\"part_index\":%u,\"part_count\":%u,\"threads_status\":\"%s\",\"rows_seen\":",
  part,count,r->threads_rc==-ENOSPC?"truncated":status(r->threads_rc));
 if(!r->threads_rc||r->threads_rc==-ENOSPC)xt_raw(w,"%zu",r->seen);else xt_raw(w,"null");
 xt_raw(w,",\"rows_returned\":%zu},\"samples\":[",r->rows);
 sample(w,p,"cpu.system.percent","percent","gauge",3,r->cpu.rc,r->cpu.percent_milli,0,cpu_status(r->cpu.rc));
 for(size_t i=part*4;i<r->rows&&i<(part+1)*4;i++){
  const struct xdr_thread *t=&r->threads[i];
  if(t->rc>0||!t->lifetime){w->error=-ERANGE;return;}
  xt_raw(w,",{\"name\":\"cpu.thread.percent\",\"unit\":\"percent\",\"kind\":\"gauge\",\"encoding\":\"float64\",\"value\":");
  if(t->rc)xt_raw(w,"null");
  else if(t->percent_milli>100000){w->error=-ERANGE;return;}
  else xt_raw(w,"%u.%03u",(unsigned)(t->percent_milli/1000),(unsigned)(t->percent_milli%1000));
  xt_raw(w,",\"status\":\"%s\",\"reason\":",cpu_status(t->rc));xt_string(w,t->rc?cpu_status(t->rc):NULL);
  xt_raw(w,",\"source_rc\":%d,\"labels\":{\"thread_id\":",t->rc);xt_u64(w,t->lifetime);
  xt_raw(w,",\"thread_name\":");xt_string(w,t->name);xt_raw(w,",\"name_truncated\":\"%s\"}}",t->name_truncated?"true":"false");
 }
 xt_raw(w,"]");
}

static void event(struct xt_writer *w,struct xt_publisher *p,const struct xdiag_event *e)
{
	if (!valid_event(e)) { w->error=-ERANGE;return; }
	xt_raw(w,"{\"event_id\":\"%s/%s/%" PRIu64 "\",\"event_seq\":",p->identity.vm_id,p->identity.boot_id,e->event_seq);xt_u64(w,e->event_seq);
	xt_raw(w,",\"occurred_uptime_ms\":");xt_u64(w,e->uptime_ms);
	xt_raw(w,",\"category\":\"%s\",\"stage\":",e->boot_event ? "boot" : "error");xt_string(w,e->stage);
	xt_raw(w,",\"source_rc\":%d,\"severity\":\"%s\",\"reason_code\":\"core_observation\",\"detail\":\"Current guest boot RAM observation\"}",e->code,e->code ? "error" : "info");
}

static void coverage(struct xt_writer *w,struct xt_publisher *p,const struct xdiag_history *h)
{
	struct xdiag_event events[16];size_t retained=history_events(h,events);
	xt_raw(w,"{\"source_total\":");xt_u64(w,h->event_sequence);
	xt_raw(w,",\"source_retained\":%zu,\"missed_before_capture\":",retained);xt_u64(w,h->event_sequence>=retained ? h->event_sequence-retained : 0);
	xt_raw(w,",\"publisher_dropped_total\":");xt_u64(w,p->counters.events_dropped);
	xt_raw(w,",\"scope\":\"current_guest_boot_ram_only\"}");
}

static void thread(struct xt_writer *w,const struct xt_thread *t,size_t row)
{
	xt_raw(w,"{\"row_id\":%zu,\"name\":",row);xt_string(w,t->name);
	xt_raw(w,",\"name_truncated\":%s,\"state_text\":",boolean(t->name_truncated));xt_string(w,t->state);
	xt_raw(w,",\"state_flags\":%u,\"priority\":%d,\"stack\":{\"status\":\"%s\",\"source_rc\":%d,\"size_bytes\":",(unsigned)t->flags,t->priority,t->stack_rc==-E2BIG ? "scan_limit" : status(t->stack_rc),t->stack_rc);xt_u64(w,t->stack_bytes);
	xt_raw(w,",\"unused_high_water_bytes\":");if (t->stack_rc) { xt_raw(w,"null"); } else { xt_u64(w,t->unused_bytes); }xt_raw(w,"}}");
}

static void snapshot(struct xt_writer *w,struct xt_publisher *p,enum xt_kind kind,
	const struct xt_observation *s,const char *id,unsigned part)
{
	struct xdiag_event events[16]; size_t total=kind==XT_THREADS ? s->rows : history_events(&s->history,events);
	int rc=kind==XT_THREADS ? s->threads_rc : s->history_rc;
	bool available=!rc || (kind==XT_THREADS && rc==-ENOSPC);
	if (!available) { total=0; }
	unsigned width=kind==XT_THREADS ? 4 : 8;
	unsigned count=(unsigned)((total+width-1)/width);if (!count) { count=1; }
	if (part>=count || count>8 || !xt_uuid_valid(id)) { w->error=-EINVAL;return; }
	xt_raw(w,",\"part\":{\"snapshot_id\":");xt_string(w,id);
	xt_raw(w,",\"index\":%u,\"count\":%u},\"source_status\":\"%s\",\"source_rc\":%d,\"kind\":\"%s\"",part,count,!rc ? "ok" : rc==-ENOSPC ? "truncated" : rc==-ENOTSUP ? "unsupported" : rc==-EAGAIN ? "not_requested" : "collection_failed",rc,kind==XT_THREADS ? "threads" : "history");
	if (kind==XT_THREADS) {
		xt_raw(w,",\"identity_scope\":\"snapshot_local\",\"rows_seen\":");
		if (available) { xt_raw(w,"%zu",s->rows_seen); } else { xt_raw(w,"null"); }
		xt_raw(w,",\"rows_returned_total\":%zu,\"rows\":[",total);
	} else { xt_raw(w,",\"events\":["); }
	for (size_t i=part*width;i<total && i<(part+1)*width;++i) {
		if (i!=part*width) { xt_raw(w,","); }
		if (kind==XT_THREADS) { thread(w,&s->threads[i],i); } else { event(w,p,&events[i]); }
	}
	xt_raw(w,"]");
	if (kind==XT_HISTORY) { xt_raw(w,",\"coverage\":");if (available) { coverage(w,p,&s->history); } else { xt_raw(w,"null"); } }
}

int xt_encode(struct xt_publisher *p,enum xt_kind kind,const struct xt_observation *s,
	uint64_t sent,const char sample_id[37],const char snapshot_id[37],unsigned part,struct xt_message *out)
{
	if (!p || !s || !out || !xt_uuid_valid(sample_id) || s->start_ms>s->end_ms || s->end_ms>sent ||
    s->rows>XT_THREAD_MAX || kind<XT_INVENTORY || kind>XT_POOLS) { return -EINVAL; }
	if ((kind==XT_HEARTBEAT || (kind>=XT_MEMORY && kind<=XT_PUBLISHER) || kind==XT_CPU || kind==XT_POOLS) && sent-s->end_ms>2000) { ++p->counters.live_discarded;return -ETIMEDOUT; }
	/* Collection copies a cache: its new end timestamp cannot renew the source
	 * window. Reject stale resource pages before allocating a stream sequence. */
	if (kind==XT_CPU || kind==XT_POOLS) {
		uint64_t source=kind==XT_CPU ? s->resources.cpu.end_ms : s->resources.raw.at_ms;
		if (source>s->end_ms) { return -EINVAL; }
		if (sent-source>2000) { ++p->counters.live_discarded;return -ETIMEDOUT; }
	}
	if (kind>=XT_INTERVAL && kind<=XT_ELAPSED && s->timing_rc) { return s->timing_rc; }
	if (kind==XT_EVENT && !p->event_count) { return -EAGAIN; }
	unsigned stream=kind==XT_INVENTORY ? 0 : kind==XT_HEARTBEAT ? 1 : kind==XT_EVENT ? 3 : (kind==XT_THREADS||kind==XT_HISTORY) ? 4 : 2;
	if (p->sequence[stream]==UINT64_MAX) { return -ERANGE; }
	if (!p->task_epoch[0] || (!s->timing_rc && (!p->task_seen || s->timing.origin_us!=p->task_origin))) {
		/* One watched Core task: boot namespace + its fixed initialization origin.
		 * Publisher restart must not pretend the continuing task was reset. */
		int rc=uuid_offset(p->identity.boot_id,s->timing_rc ? 0 : s->timing.origin_us+1,p->task_epoch);if (rc) { return rc; }
		p->task_seen=!s->timing_rc;p->task_origin=s->timing.origin_us;
	}
	memset(out,0,sizeof(*out)); out->stream=stream;out->qos=stream==0 || stream>=3;out->retain=stream==0;
	(void)snprintf(out->topic,sizeof(out->topic),"xenova/v2/vms/%s/telemetry/%s",p->identity.vm_id,streams[stream]);
	struct xt_writer w={.data=out->bytes,.size=sizeof(out->bytes)};
	uint64_t seq=p->sequence[stream]+1;
	xt_raw(&w,"{\"schema_version\":\"2.1\",\"type\":\"telemetry.%s\",\"message_id\":\"%s/%s/%s/%s/%" PRIu64 "\",\"vm_id\":",streams[stream],p->identity.vm_id,p->identity.boot_id,p->identity.publisher_id,streams[stream],seq);
	xt_string(&w,p->identity.vm_id);xt_raw(&w,",\"boot_id\":");xt_string(&w,p->identity.boot_id);
	xt_raw(&w,",\"publisher_instance_id\":");xt_string(&w,p->identity.publisher_id);
	xt_raw(&w,",\"seq\":");xt_u64(&w,seq);
	bool bound=p->identity.host_boot_id[0];
	xt_raw(&w,",\"context\":{\"binding\":\"%s\",\"host_boot_id\":",bound ? "host_provisioned" : "unbound");xt_string(&w,bound ? p->identity.host_boot_id : NULL);
	xt_raw(&w,",\"execution_id\":");xt_string(&w,bound ? p->identity.execution_id : NULL);
	xt_raw(&w,",\"receipt_id\":");xt_string(&w,bound ? p->identity.receipt_id : NULL);
	xt_raw(&w,"},\"clock\":{\"sent_uptime_ms\":");xt_u64(&w,sent);
	char iso[25];uint64_t error;
	if(xt_utc_read(&p->utc,sent,iso,&error)){
	 xt_raw(&w,",\"sent_at\":null,\"utc_quality\":\"unavailable\",\"utc_source\":null,\"uncertainty_ms\":null}");
	}else{
	 xt_raw(&w,",\"sent_at\":");xt_string(&w,iso);xt_raw(&w,",\"utc_quality\":\"synchronized\",\"utc_source\":");
	 xt_string(&w,p->utc.source);xt_raw(&w,",\"uncertainty_ms\":%" PRIu64 "}",error);
	}
	xt_raw(&w,",\"payload\":{");
	collection(&w,s,sample_id);
	if (kind==XT_INVENTORY) { inventory(&w,p,s); }
	else if (kind==XT_HEARTBEAT) { heartbeat(&w,&p->counters); }
	else if (kind>=XT_INTERVAL && kind<=XT_ELAPSED) { distribution(&w,p,kind,s); }
	else if (kind==XT_EVENT) { xt_raw(&w,",\"event\":");event(&w,p,&p->events[0]);xt_raw(&w,",\"coverage\":");coverage(&w,p,&s->history); }
	else if (kind==XT_THREADS||kind==XT_HISTORY) { snapshot(&w,p,kind,s,snapshot_id,part); }
	else if (kind==XT_CPU||kind==XT_POOLS) { resources(&w,p,kind,s,part); }
	else { metrics(&w,p,kind,s); }
	xt_raw(&w,"}}");
	if (w.error) { out->bytes[0]=0;out->length=0;if (w.error==-ENOSPC) { ++p->counters.payload_oversize; }return w.error; }
	out->length=w.used;p->sequence[stream]=seq;return 0;
}
