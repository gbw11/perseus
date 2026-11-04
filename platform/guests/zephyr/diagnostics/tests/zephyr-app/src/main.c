/* SPDX-License-Identifier: Apache-2.0 */
/* Real Zephyr adapter tests; network callback is an explicit fault fixture. */
#include <xenova/diagnostics.h>
#include <xenova/health.h>
#include <zephyr/ztest.h>
#include <zephyr/shell/shell.h>
#include <zephyr/shell/shell_dummy.h>
#include <errno.h>
#include <string.h>
#if defined(CONFIG_XENOVA_DIAGNOSTICS_POOLS)
#include <zephyr/net_buf.h>
#include <zephyr/sys/iterable_sections.h>
static struct net_buf_pool *rx_pool(void){
 STRUCT_SECTION_FOREACH(net_buf_pool,pool)if(pool->name&&!strcmp(pool->name,"rx_bufs"))return pool;
 return NULL;
}
ZTEST(diagnostics,test_fixed_pool_actual_allocate_release)
{
 struct net_buf_pool *pool=rx_pool();zassert_not_null(pool);
 k_sleep(K_MSEC(250));struct xdr_snapshot before,during,after;
 zassert_ok(xdiag_resources_get(&before));
 struct net_buf *held=net_buf_alloc(pool,K_NO_WAIT);zassert_not_null(held);
 k_sleep(K_MSEC(250));zassert_ok(xdiag_resources_get(&during));
 net_buf_unref(held);k_sleep(K_MSEC(250));zassert_ok(xdiag_resources_get(&after));
 bool found=false;
 for(size_t i=0;i<during.regions;i++)if(!strcmp(during.memory[i].id,"net_rx_data")){
  zassert_ok(during.memory[i].rc);zassert_equal(during.memory[i].used,before.memory[i].used+CONFIG_NET_BUF_DATA_SIZE);
  zassert_equal(after.memory[i].used,before.memory[i].used);
  zassert_equal(during.memory[i].capacity,during.memory[i].used+during.memory[i].free);found=true;
 }
 zassert_true(found);
}
#endif

static struct xdiag_snapshot snapshot;
static struct xdiag_loop loop;
extern struct k_mutex resource_cache_lock;
K_SEM_DEFINE(health_locked,0,1);
K_SEM_DEFINE(health_release,0,1);
static struct k_thread health_thread;
K_THREAD_STACK_DEFINE(health_stack,2048);
static void health_hold(void *a,void *b,void *c)
{
 ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);
 k_mutex_lock(&resource_cache_lock,K_FOREVER);k_sem_give(&health_locked);
 k_sem_take(&health_release,K_FOREVER);k_mutex_unlock(&resource_cache_lock);
}
ZTEST(diagnostics,test_health_uses_actual_heap_and_nonwaiting_cache)
{
 struct xdr_snapshot resource;struct xdh_health health;
 k_sleep(K_MSEC(250));zassert_ok(xdiag_resources_try_get(&resource));
 zassert_ok(resource.heap_rc);zassert_true(resource.heap_free_bytes>0);
 void *impossible=k_malloc(CONFIG_HEAP_MEM_POOL_SIZE*2);zassert_is_null(impossible);
 struct xdh_work work={.state=XDH_COMPLETED,.required=true,.rc=-ENOMEM,.deadline_us=20000};
 zassert_ok(xdh_assess(&work,1000,k_uptime_get(),&resource,0,&health));
 zassert_true(health.restrict_function);zassert_equal(health.failure,XDH_RESOURCE_FAILED);
 work.required=false;zassert_ok(xdh_assess(&work,1000,k_uptime_get(),&resource,0,&health));
 zassert_false(health.restrict_function);
 k_thread_create(&health_thread,health_stack,K_THREAD_STACK_SIZEOF(health_stack),health_hold,NULL,NULL,NULL,5,0,K_NO_WAIT);
 zassert_ok(k_sem_take(&health_locked,K_SECONDS(1)));
 int64_t begin=k_uptime_get();int rc=xdiag_resources_try_get(&resource);int64_t elapsed=k_uptime_get()-begin;
 k_sem_give(&health_release);zassert_ok(k_thread_join(&health_thread,K_SECONDS(1)));
 zassert_equal(rc,-EBUSY);zassert_true(elapsed<20);
 work=(struct xdh_work){.state=XDH_COMPLETED,.required=true,.begin_us=100,.end_us=1000,.deadline_us=20000};
 zassert_ok(xdh_assess(&work,1000,k_uptime_get(),NULL,rc,&health));
 zassert_false(health.restrict_function);zassert_false(health.cpu_valid);
 printk("HEALTH_NATIVE required_heap_failure=restricted diagnostic_failure=local cache_busy=optional elapsed_ms=%lld\n",elapsed);
}
static int provider_rc;
static uint64_t byte_tx,byte_rx,byte_epoch=1;
static int provider(struct xdiag_network *out, void *context)
{
	ARG_UNUSED(context);
	out->tx = 42;
	out->link_up = true;
	out->tx_bytes=byte_tx;out->rx_bytes=byte_rx;out->counter_epoch=byte_epoch;
	return provider_rc;
}
SHELL_CMD_REGISTER(diag, NULL, "Local diagnostics test", xdiag_shell_cmd);

ZTEST(diagnostics, test_actual_threads_heap_and_errors)
{
	zassert_ok(xdiag_boot("test-stage", 0));
	zassert_ok(xdiag_error("test-failure", -EIO));
	zassert_ok(xdiag_error("진단", -EIO));
	zassert_ok(xdiag_error("quote\"\\\n", -EIO));
	zassert_ok(xdiag_snapshot_get(&snapshot, true));
	zassert_true(snapshot.history.error_count >= 1);
	zassert_equal(snapshot.history.errors[snapshot.history.error_entries - 1].code, -EIO);
#if defined(CONFIG_XENOVA_DIAGNOSTICS_THREADS)
	if (snapshot.threads_seen > ARRAY_SIZE(snapshot.threads)) {
		zassert_equal(snapshot.threads_rc, -ENOSPC);
	} else { zassert_ok(snapshot.threads_rc); }
	zassert_true(snapshot.thread_count > 0);
	bool found = false;
	for (size_t i = 0; i < snapshot.thread_count; ++i) {
		struct xdiag_thread *t = &snapshot.threads[i];
		if (!t->stack_rc) {
			zassert_true(t->stack_bytes > 0);
			zassert_true(t->unused_bytes <= t->stack_bytes);
			found = true;
		} else {
			zassert_equal(t->stack_rc, -E2BIG);
		}
	}
	if (CONFIG_XENOVA_DIAGNOSTICS_STACK_SCAN_LIMIT >= CONFIG_TEST_EXTRA_STACK_SIZE + 1024) {
		zassert_true(found);
	}
#else
	zassert_equal(snapshot.threads_rc, -ENOTSUP);
	zassert_equal(snapshot.thread_count, 0);
#endif
#if defined(CONFIG_XENOVA_DIAGNOSTICS_HEAP)
	zassert_ok(snapshot.heap_rc);
	size_t before = snapshot.heap_allocated_bytes;
	void *allocation = k_malloc(256);
	zassert_not_null(allocation);
	zassert_ok(xdiag_snapshot_get(&snapshot, false));
	zassert_true(snapshot.heap_allocated_bytes >= before + 256);
	zassert_true(snapshot.heap_peak_bytes >= snapshot.heap_allocated_bytes);
	k_free(allocation);
	zassert_ok(xdiag_snapshot_get(&snapshot, false));
	zassert_equal(snapshot.heap_allocated_bytes, before);
#else
	zassert_equal(snapshot.heap_rc, -ENOTSUP);
#endif
	zassert_ok(xdiag_snapshot_get(&snapshot, false));
	zassert_equal(snapshot.threads_rc, -EAGAIN);
}

ZTEST(diagnostics, test_clock_and_completed_progress)
{
#if !defined(CONFIG_XENOVA_DIAGNOSTICS_TIMING)
	uint64_t out = 123;
	zassert_equal(xdiag_clock_us(&out), -ENOTSUP);
	zassert_equal(out, 123);
	zassert_equal(xdiag_loop_init(&loop, 20000, 20000), -ENOTSUP);
	zassert_ok(xdiag_snapshot_get(&snapshot, false));
	zassert_equal(snapshot.clock_rc, -ENOTSUP);
	zassert_equal(snapshot.timing_rc, -ENOENT);
#else
	uint64_t start, end;
	zassert_ok(xdiag_clock_us(&start));
	int64_t uptime = k_uptime_get();
	k_sleep(K_MSEC(20));
	zassert_ok(xdiag_clock_us(&end));
	zassert_true(end > start);
	int64_t discrepancy = (int64_t)(end - start) - (k_uptime_get() - uptime) * 1000;
	zassert_true(discrepancy > -2000 && discrepancy < 2000);
	zassert_ok(xdiag_loop_init(&loop, 20000, 20000));
	zassert_ok(xdiag_loop_watch(&loop));
	zassert_ok(xdiag_loop_begin(&loop));
	zassert_ok(xdiag_snapshot_get(&snapshot, false));
	zassert_true(snapshot.timing.in_flight);
	zassert_equal(snapshot.timing.loop_seq, 0);
	k_sleep(K_MSEC(2));
	zassert_ok(xdiag_loop_end(&loop));
	zassert_ok(xdiag_snapshot_get(&snapshot, false));
	zassert_equal(snapshot.timing.loop_seq, 1);
	zassert_false(snapshot.timing.in_flight);
	zassert_true(snapshot.timing.elapsed.last >= 2000);
	zassert_ok(snapshot.clock_rc);
	zassert_true(snapshot.cycle_hz > 0);
#endif
}

ZTEST(diagnostics, test_network_failure_and_shell_json)
{
	int registration=xdiag_network_register(provider,NULL);
	zassert_true(registration==0||registration==-EALREADY);
	zassert_equal(xdiag_network_register(provider, NULL), -EALREADY);
	const struct shell *sh = shell_backend_dummy_get_ptr();
	for (unsigned i = 0; !shell_ready(sh) && i < 100; ++i) { k_sleep(K_MSEC(1)); }
	zassert_true(shell_ready(sh));
	shell_backend_dummy_clear_output(sh);
	provider_rc = -ETIMEDOUT;
	zassert_ok(shell_execute_cmd(sh, "diag"));
	size_t size;
	const char *output = shell_backend_dummy_get_output(sh, &size);
	zassert_not_null(strstr(output, "\"network\":{\"status\":\"collection_failed\""));
	zassert_not_null(strstr(output, "\"value\":null"));
	printk("XDIAG_JSON_FAILURE %s\n", output);
	provider_rc = 0;
	shell_backend_dummy_clear_output(sh);
	zassert_ok(shell_execute_cmd(sh, "diag threads"));
	output = shell_backend_dummy_get_output(sh, &size);
	zassert_not_null(strstr(output, "\"tx\":42"));
	zassert_not_null(strstr(output, "\"status\":\"ok\""));
	printk("XDIAG_JSON_SUCCESS %s\n", output);
	zassert_equal(shell_execute_cmd(sh, "diag unsupported"), -EINVAL);
}
ZTEST_SUITE(diagnostics, NULL, NULL, NULL, NULL, NULL);

static void next_resource(struct xdr_snapshot *r)
{
 uint64_t previous=r->sample;int64_t end=k_uptime_get()+1000;
 do{k_sleep(K_MSEC(5));zassert_ok(xdiag_resources_get(r));}while(r->sample==previous&&k_uptime_get()<end);
 zassert_not_equal(r->sample,previous);
}
ZTEST(diagnostics,test_network_byte_rate_epoch_and_failure)
{
 int rc=xdiag_network_register(provider,NULL);zassert_true(rc==0||rc==-EALREADY);
 provider_rc=0;struct xdr_snapshot r;zassert_ok(xdiag_resources_get(&r));
 next_resource(&r);next_resource(&r);
 byte_tx+=64;byte_rx+=128;next_resource(&r);
 zassert_ok(r.net_bytes_rc);zassert_equal(r.rx_bytes,byte_rx);
 zassert_ok(r.net_rate_rc);uint64_t dt=r.net_end_ms-r.net_start_ms;
 zassert_true(dt>0);zassert_equal(r.tx_bytes_per_s,64000/dt);zassert_equal(r.rx_bytes_per_s,128000/dt);
 next_resource(&r);zassert_ok(r.net_rate_rc);zassert_equal(r.rx_bytes_per_s,0);
 byte_epoch++;byte_tx=byte_rx=0;next_resource(&r);zassert_equal(r.net_rate_rc,-EOVERFLOW);
 zassert_ok(r.net_bytes_rc);zassert_equal(r.net_epoch,byte_epoch);zassert_equal(r.rx_bytes,0);
 provider_rc=-EIO;next_resource(&r);zassert_equal(r.net_rate_rc,-EIO);
 zassert_equal(r.net_bytes_rc,-EIO);
 provider_rc=0;next_resource(&r);zassert_equal(r.net_rate_rc,-ENODATA);
 zassert_ok(r.net_bytes_rc);
}

#if defined(CONFIG_XENOVA_DIAGNOSTICS_CPU)
static struct k_thread crowded[70];
K_THREAD_STACK_ARRAY_DEFINE(crowded_stacks,70,1024);
static void parked(void *a,void *b,void *c)
{ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);k_sleep(K_FOREVER);}
ZTEST(diagnostics,test_registry_and_return_limits_are_explicit)
{
 for(unsigned i=0;i<ARRAY_SIZE(crowded);i++)
  k_thread_create(&crowded[i],crowded_stacks[i],K_THREAD_STACK_SIZEOF(crowded_stacks[i]),parked,NULL,NULL,NULL,8,0,K_NO_WAIT);
 k_sleep(K_MSEC(450));struct xdr_snapshot r;zassert_ok(xdiag_resources_get(&r));
 zassert_equal(r.rows,XDR_THREADS);zassert_true(r.seen>64);zassert_equal(r.threads_rc,-ENOSPC);
 for(size_t i=0;i<r.rows;i++){
  zassert_true(r.threads[i].lifetime>0);
  for(size_t j=0;j<i;j++)zassert_not_equal(r.threads[i].lifetime,r.threads[j].lifetime);
 }
 printk("RESOURCE_LIMIT rows=%zu seen=%zu rc=%d\n",r.rows,r.seen,r.threads_rc);
 for(unsigned i=0;i<ARRAY_SIZE(crowded);i++){k_thread_abort(&crowded[i]);zassert_ok(k_thread_join(&crowded[i],K_MSEC(200)));}
 k_sleep(K_MSEC(450));zassert_ok(xdiag_resources_get(&r));zassert_ok(r.threads_rc);zassert_true(r.seen<32);
}
#if defined(XDR_TEST_LONG_GAP)
K_SEM_DEFINE(gap_started,0,1);
K_SEM_DEFINE(gap_ended,0,1);
static uint64_t gap_ms;
static void block_collector(struct k_work *work)
{ARG_UNUSED(work);k_sem_give(&gap_started);k_sleep(K_MSEC(gap_ms));k_sem_give(&gap_ended);}
K_WORK_DEFINE(blocked_work,block_collector);
ZTEST(diagnostics,test_long_gap_baseline_and_two_normal_windows)
{
 k_sleep(K_MSEC(450));struct xdr_snapshot r;zassert_ok(xdiag_resources_get(&r));zassert_ok(r.cpu.rc);
 gap_ms=((uint64_t)UINT32_MAX*1000/sys_clock_hw_cycles_per_sec())/2+500;
 k_work_submit(&blocked_work);zassert_ok(k_sem_take(&gap_started,K_SECONDS(1)));
 k_sleep(K_MSEC(1100));zassert_ok(xdiag_resources_get(&r));zassert_equal(r.cpu.rc,-EOVERFLOW);
 zassert_ok(k_sem_take(&gap_ended,K_MSEC(gap_ms)));next_resource(&r);
 zassert_equal(r.cpu.rc,-EOVERFLOW);uint64_t anchor=r.raw.at_ms;
 next_resource(&r);zassert_ok(r.cpu.rc);zassert_equal(r.cpu.start_ms,anchor);zassert_true(r.cpu.total_delta>0);
 for(size_t i=0;i<r.rows;i++)zassert_ok(r.threads[i].rc);
 anchor=r.raw.at_ms;next_resource(&r);zassert_ok(r.cpu.rc);zassert_equal(r.cpu.start_ms,anchor);
 printk("RESOURCE_GAP recovered gap_ms=%llu sample=%llu\n",gap_ms,r.sample);
}
#endif
static struct k_thread measured_thread;
K_THREAD_STACK_DEFINE(measured_stack,2048);
K_SEM_DEFINE(measured_done,0,1);
static void measured(void *a,void *b,void *c)
{
 ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);
 for(unsigned i=0;i<5;i++){k_busy_wait(20000);k_sleep(K_MSEC(50));}
 k_sem_give(&measured_done);k_sleep(K_FOREVER);
}
static uint64_t measured_id(void)
{
 struct xdr_snapshot r;zassert_ok(xdiag_resources_get(&r));
 for(size_t i=0;i<r.rows;i++)if(!strcmp(r.threads[i].name,"same-name"))return r.threads[i].lifetime;
 return 0;
}
ZTEST(diagnostics,test_runtime_source_and_lifetime_reuse)
{
 k_sleep(K_MSEC(450));
 struct xdr_snapshot a,b;zassert_ok(xdiag_resources_get(&a));
 zassert_ok(a.cpu.rc);zassert_true(a.cpu.total_delta>0);zassert_true(a.cpu.percent_milli<=100000);
 zassert_ok(xdiag_resources_get(&b));zassert_equal(a.sample,b.sample); /* read never changes baseline */
 k_thread_create(&measured_thread,measured_stack,K_THREAD_STACK_SIZEOF(measured_stack),measured,NULL,NULL,NULL,5,0,K_FOREVER);
 zassert_ok(k_thread_name_set(&measured_thread,"same-name"));k_thread_start(&measured_thread);
 bool saw_busy=false;uint32_t peak=0;int64_t limit=k_uptime_get()+2000;
 while(k_sem_take(&measured_done,K_NO_WAIT)){
  zassert_true(k_uptime_get()<limit);k_sleep(K_MSEC(10));struct xdr_snapshot load;
  zassert_ok(xdiag_resources_get(&load));
  for(size_t i=0;i<load.rows;i++)if(!strcmp(load.threads[i].name,"same-name")&&!load.threads[i].rc){
   peak=MAX(peak,load.threads[i].percent_milli);saw_busy|=load.threads[i].percent_milli>0;
  }
 }
 zassert_true(saw_busy);zassert_true(peak<=100000);k_sleep(K_MSEC(220));
 uint64_t first=measured_id();zassert_true(first>0);
 k_thread_abort(&measured_thread);zassert_ok(k_thread_join(&measured_thread,K_MSEC(200)));
 k_thread_create(&measured_thread,measured_stack,K_THREAD_STACK_SIZEOF(measured_stack),measured,NULL,NULL,NULL,5,0,K_FOREVER);
 zassert_ok(k_thread_name_set(&measured_thread,"same-name"));k_thread_start(&measured_thread);
 zassert_ok(k_sem_take(&measured_done,K_SECONDS(2)));k_sleep(K_MSEC(220));
 uint64_t second=measured_id();zassert_true(second>0);zassert_not_equal(first,second);
 k_thread_abort(&measured_thread);zassert_ok(k_thread_join(&measured_thread,K_MSEC(200)));
 zassert_ok(xdiag_snapshot_get(&snapshot,false));zassert_equal(snapshot.threads_rc,-EAGAIN);
 printk("RESOURCE_EVIDENCE samples=%llu total=%llu busy=%llu first=%llu reused=%llu load_peak_milli=%u\n",
  b.sample,b.cpu.total_delta,b.cpu.busy_delta,first,second,peak);
}
#endif
