/* SPDX-License-Identifier: Apache-2.0 */
/* One system-workqueue owner, boot lifetime. Readers only copy the bounded cache.
 * Lock order: thread monitor -> registry; neither registry nor cache enters the
 * monitor. Runtime API has its own usage lock. No stack, Store or MQTT calls. */
#include <xenova/diagnostics.h>
#include <zephyr/init.h>
#include <zephyr/sys/iterable_sections.h>
#include <zephyr/sys/sys_heap.h>
#include <errno.h>
#include <string.h>
#include <stdatomic.h>
#if defined(CONFIG_BOARD_XENVM)
#include <zephyr/kernel/mm.h>
#endif
#if defined(CONFIG_XENOVA_DIAGNOSTICS_POOLS)
#include <zephyr/net_buf.h>
#endif
K_MUTEX_DEFINE(resource_cache_lock);
static struct xdr_snapshot cache;
static struct xdiag_network net_previous;
static uint64_t net_at;
static atomic_int app_memory_state; /* 0 absent, 1 installing, 2 immutable/ready */
static xdiag_app_memory_provider app_memory;
static void *app_memory_context;
static unsigned app_memory_count;
int xdiag_app_memory_register(xdiag_app_memory_provider provider,void *context,unsigned count){
 if(!provider||k_is_in_isr()||(count!=XDR_AUTO_REGIONS&&count!=XDR_APP_REGIONS))return -EINVAL;
 int absent=0;
 if(!atomic_compare_exchange_strong(&app_memory_state,&absent,1))return -EALREADY;
 app_memory_context=context;app_memory=provider;app_memory_count=count;
 atomic_store_explicit(&app_memory_state,2,memory_order_release);return 0;
}

#if defined(CONFIG_XENOVA_DIAGNOSTICS_CPU) && defined(CONFIG_SCHED_THREAD_USAGE_AUTO_ENABLE) && \
 !defined(CONFIG_THREAD_RUNTIME_STATS_USE_TIMING_FUNCTIONS) && defined(CONFIG_TIMER_HAS_64BIT_CYCLE_COUNTER)
#define XDR_CPU_LIVE 1
#endif
bool xdiag_cpu_supported(void){return IS_ENABLED(XDR_CPU_LIVE);}
#if defined(XDR_CPU_LIVE)
BUILD_ASSERT(IS_ENABLED(CONFIG_TIMER_HAS_64BIT_CYCLE_COUNTER),"CPU continuity requires the pinned 64-bit clock");
BUILD_ASSERT(IS_ENABLED(CONFIG_TRACING_USER),"CPU thread lifetime hooks require tracing-user");
struct lifetime { const struct k_thread *object; uint64_t id, previous; bool sampled,seen; };
static struct lifetime registry[64];
static uint64_t next_lifetime;
static struct k_spinlock registry_lock;
static struct xdr_counter previous;
static bool sampled;
static struct xdr_snapshot collecting;
static bool continuity;

static struct lifetime *find(const struct k_thread *object,bool renew)
{
 struct lifetime *empty=NULL;
 for(size_t i=0;i<ARRAY_SIZE(registry);i++){
  if(registry[i].object==object){if(!renew)return &registry[i];empty=&registry[i];break;}
  if(!registry[i].object&&!empty)empty=&registry[i];
 }
 if(!empty||next_lifetime==UINT64_MAX)return NULL;
 *empty=(struct lifetime){.object=object,.id=++next_lifetime};return empty;
}
/* Frozen tracing-user hooks only track object lifetime, never generate app
 * progress or control readiness. Address reuse gets a fresh non-pointer ID. */
void sys_trace_thread_create_user(struct k_thread *thread)
{
 k_spinlock_key_t key=k_spin_lock(&registry_lock);(void)find(thread,true);k_spin_unlock(&registry_lock,key);
}
void sys_trace_thread_abort_user(struct k_thread *thread)
{
 k_spinlock_key_t key=k_spin_lock(&registry_lock);
 for(size_t i=0;i<ARRAY_SIZE(registry);i++)if(registry[i].object==thread)registry[i]=(struct lifetime){0};
 k_spin_unlock(&registry_lock,key);
}
static void row(const struct k_thread *thread,void *ctx)
{
 struct xdr_snapshot *s=ctx;s->seen++;
 /* The CPU API flushed current accounting once at the common anchor. UP IRQ
  * exclusion keeps every raw thread total at that same boundary. Calling the
  * thread API here would advance the current thread to a different denominator. */
 uint64_t accounted=thread->base.usage.total;
 int rc=thread->base.usage.track_usage?0:-ENOTSUP;
 k_spinlock_key_t key=k_spin_lock(&registry_lock);
 struct lifetime *life=find(thread,false);
 uint64_t prior=life?life->previous:0;bool had_sample=life&&life->sampled;
 if(life){life->seen=true;life->previous=accounted;life->sampled=!rc;}
 if(s->rows==XDR_THREADS||!life){s->threads_rc=-ENOSPC;k_spin_unlock(&registry_lock,key);return;}
 struct xdr_thread *r=&s->threads[s->rows++];r->rc=rc;r->lifetime=life->id;
 const char *name=k_thread_name_get((k_tid_t)thread);
 if(!name)name="unnamed";
 size_t bytes=strlen(name);r->name_truncated=bytes>=sizeof(r->name);
 memcpy(r->name,name,MIN(bytes,sizeof(r->name)-1));
 r->accounted=accounted;r->previous=prior;
 if(!rc&&(!had_sample||!continuity))r->rc=-ENODATA;
 if(!rc&&had_sample&&accounted<prior)r->rc=-EOVERFLOW;
 k_spin_unlock(&registry_lock,key);
}
#endif

static void collect(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(resource_work,collect);
static void collect(struct k_work *work)
{
 ARG_UNUSED(work);
#if defined(CONFIG_TIMER_HAS_64BIT_CYCLE_COUNTER)
 uint64_t begin=k_cyc_to_us_floor64(k_cycle_get_64());
#endif
 static struct xdr_snapshot result;memset(&result,0,sizeof(result));
#if defined(XDR_CPU_LIVE)
 uint64_t hz=sys_clock_hw_cycles_per_sec();
 /* usage.c uses uint32_t cycles even when its accumulator is uint64_t.
  * Half a wrap is a conservative observation limit, not a repair heuristic. */
 uint64_t max_gap=hz?((uint64_t)UINT32_MAX*1000/hz)/2:0;
 uint64_t now=k_uptime_get();
 continuity=sampled&&max_gap&&now>previous.at_ms&&now-previous.at_ms<max_gap;
 memset(&collecting,0,sizeof(collecting));
 k_spinlock_key_t registry_key=k_spin_lock(&registry_lock);
 uint64_t fence=next_lifetime;
 for(size_t i=0;i<ARRAY_SIZE(registry);i++)registry[i].seen=false;
 k_spin_unlock(&registry_lock,registry_key);
 k_thread_runtime_stats_t raw={0};
 unsigned int irq_key=irq_lock();
 int rc=k_thread_runtime_stats_cpu_get(0,&raw);
 uint64_t anchor=k_uptime_get();
 k_thread_foreach(row,&collecting);
 irq_unlock(irq_key);
 registry_key=k_spin_lock(&registry_lock);
 /* Reap terminated objects using the complete monitor enumeration too. Some
  * pinned Zephyr abort tracing paths are no-ops. Never reap a post-fence create. */
 for(size_t i=0;i<ARRAY_SIZE(registry);i++)
  if(registry[i].object&&registry[i].id<=fence&&!registry[i].seen)registry[i]=(struct lifetime){0};
 k_spin_unlock(&registry_lock,registry_key);
 result=collecting;
 result.raw=(struct xdr_counter){.total=raw.execution_cycles,.busy=raw.total_cycles,.at_ms=anchor};
 xdr_window(sampled?&previous:NULL,&result.raw,max_gap,&result.cpu);
 if(rc)result.cpu.rc=rc;
 if(sampled&&!continuity)result.cpu.rc=-EOVERFLOW;
 for(size_t i=0;i<result.rows;i++){
  struct xdr_thread *r=&result.threads[i];
  if(result.cpu.rc)r->rc=result.cpu.rc;
  if(!r->rc)r->rc=xdr_percent(r->accounted-r->previous,result.cpu.total_delta,&r->percent_milli);
 }
 previous=result.raw;sampled=!rc;
#else
 result.cpu.rc=-ENOTSUP;result.threads_rc=-ENOTSUP;
 result.raw.at_ms=k_uptime_get();
 result.cpu.start_ms=result.cpu.end_ms=result.raw.at_ms;
#endif
#if defined(CONFIG_XENOVA_DIAGNOSTICS_HEAP)
 extern struct k_heap _system_heap;
 struct sys_memory_stats heap;
 k_spinlock_key_t heap_key=k_spin_lock(&_system_heap.lock);
 result.heap_rc=sys_heap_runtime_stats_get(&_system_heap.heap,&heap);
 k_spin_unlock(&_system_heap.lock,heap_key);
 if(!result.heap_rc){result.heap_allocated_bytes=heap.allocated_bytes;result.heap_free_bytes=heap.free_bytes;}
#else
 result.heap_rc=-ENOTSUP;
#endif
#if defined(CONFIG_XENOVA_DIAGNOSTICS_POOLS)
 STRUCT_SECTION_FOREACH(net_buf_pool,pool){
  if(!pool->name||(strcmp(pool->name,"rx_bufs")&&strcmp(pool->name,"tx_bufs")))continue;
  if(result.regions==XDR_REGIONS)break;
  struct xdr_region *r=&result.memory[result.regions++];
  strcpy(r->id,!strcmp(pool->name,"rx_bufs")?"net_rx_data":"net_tx_data");
  size_t free=net_buf_get_available(pool);
  r->rc=free>pool->buf_count?-ERANGE:0;
  r->capacity=(uint64_t)pool->buf_count*CONFIG_NET_BUF_DATA_SIZE;
  if(!r->rc){r->free=(uint64_t)free*CONFIG_NET_BUF_DATA_SIZE;r->used=r->capacity-r->free;}
 }
#endif
 /* Registration publishes one boot-lifetime callback with release/acquire.
  * No collector-side lock or silent missing provider on cache contention. */
 result.app_registered=atomic_load_explicit(&app_memory_state,memory_order_acquire)==2;
 if(result.app_registered){result.app_count=app_memory_count;app_memory(result.app,result.app_buffer,app_memory_context);}
 result.firmware_rc=result.page_rc=-ENOTSUP;
#if defined(CONFIG_BOARD_XENVM)
 extern char z_mapped_start[],z_mapped_end[],_image_ram_start[],_image_ram_end[];
 result.ram_region_bytes=(uint64_t)CONFIG_SRAM_SIZE*1024;
 result.mapped_bytes=(uintptr_t)z_mapped_end-(uintptr_t)z_mapped_start;
 result.static_data_bytes=(uintptr_t)_image_ram_end-(uintptr_t)_image_ram_start;
 result.firmware_rc=0;
 result.page_free_bytes=k_mem_free_get();result.page_rc=0;
#endif
 struct xdiag_network net={0};int net_rc=xdiag_network_read(&net);
 result.net_bytes_rc=net_rc?net_rc:net.bytes_rc;
 result.net_rate_rc=result.net_bytes_rc;
 result.net_start_ms=net_at;result.net_end_ms=k_uptime_get();
 result.tx_bytes=net.tx_bytes;result.rx_bytes=net.rx_bytes;result.net_epoch=net.counter_epoch;
 if(!result.net_rate_rc){
  uint64_t dt=result.net_end_ms-net_at;
  if(!net_at)result.net_rate_rc=-ENODATA;
  else if(!dt||net.counter_epoch!=net_previous.counter_epoch||
   net.tx_bytes<net_previous.tx_bytes||net.rx_bytes<net_previous.rx_bytes)result.net_rate_rc=-EOVERFLOW;
  else if(dt>10000)result.net_rate_rc=-ETIMEDOUT;
  else if(net.tx_bytes-net_previous.tx_bytes>UINT64_MAX/1000||net.rx_bytes-net_previous.rx_bytes>UINT64_MAX/1000)result.net_rate_rc=-ERANGE;
  else{result.tx_bytes_per_s=(net.tx_bytes-net_previous.tx_bytes)*1000/dt;result.rx_bytes_per_s=(net.rx_bytes-net_previous.rx_bytes)*1000/dt;}
  net_previous=net;net_at=result.net_end_ms;
 }else net_at=0;
 k_mutex_lock(&resource_cache_lock,K_FOREVER);
#if defined(CONFIG_TIMER_HAS_64BIT_CYCLE_COUNTER)
 result.collect_us=k_cyc_to_us_floor64(k_cycle_get_64())-begin;
 result.total_collect_us=UINT64_MAX-cache.total_collect_us<result.collect_us?UINT64_MAX:cache.total_collect_us+result.collect_us;
 result.max_collect_us=MAX(cache.max_collect_us,result.collect_us);
#endif
 result.sample=cache.sample==UINT64_MAX?UINT64_MAX:cache.sample+1;
 if(cache.sample==UINT64_MAX){result.cpu.rc=-EOVERFLOW;for(size_t i=0;i<result.rows;i++)result.threads[i].rc=-EOVERFLOW;}
 cache=result;
 k_mutex_unlock(&resource_cache_lock);
 (void)k_work_reschedule(&resource_work,K_MSEC(200));
}
static int resources_get(struct xdr_snapshot *out,k_timeout_t wait)
{
 if(!out||k_is_in_isr())return -EINVAL;
 if(k_mutex_lock(&resource_cache_lock,wait))return -EBUSY;
 *out=cache;k_mutex_unlock(&resource_cache_lock);
 uint64_t now=k_uptime_get();
 if(out->sample&&(now<out->raw.at_ms||now-out->raw.at_ms>1000)){
  if(out->cpu.rc!=-ENOTSUP)out->cpu.rc=-EOVERFLOW;
  for(size_t i=0;i<out->rows;i++)out->threads[i].rc=-EOVERFLOW;
  for(size_t i=0;i<out->regions;i++)out->memory[i].rc=-EOVERFLOW;
  if(out->heap_rc!=-ENOTSUP)out->heap_rc=-EOVERFLOW;
  if(out->page_rc!=-ENOTSUP)out->page_rc=-EOVERFLOW;
  for(unsigned i=0;i<XDR_APP_REGIONS;i++)if(out->app_registered)out->app[i].rc=-ETIMEDOUT;
  if(!out->net_bytes_rc)out->net_bytes_rc=-ETIMEDOUT;
  if(!out->net_rate_rc)out->net_rate_rc=-ETIMEDOUT;
 }
 return 0;
}
int xdiag_resources_get(struct xdr_snapshot *out){return resources_get(out,K_MSEC(20));}
int xdiag_resources_try_get(struct xdr_snapshot *out){return resources_get(out,K_NO_WAIT);}
static int init(void)
{
 cache.firmware_rc=cache.page_rc=-ENODATA;
 cache.cpu.rc=xdiag_cpu_supported()?-ENODATA:-ENOTSUP;
 cache.threads_rc=xdiag_cpu_supported()?-ENODATA:-ENOTSUP;
 cache.heap_rc=IS_ENABLED(CONFIG_XENOVA_DIAGNOSTICS_HEAP)?-ENODATA:-ENOTSUP;
 cache.net_bytes_rc=cache.net_rate_rc=-ENODATA;
 (void)k_work_reschedule(&resource_work,K_MSEC(200));return 0;
}
SYS_INIT(init,APPLICATION,CONFIG_KERNEL_INIT_PRIORITY_DEFAULT);
