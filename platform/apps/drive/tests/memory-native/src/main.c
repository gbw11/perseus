/* SPDX-License-Identifier: Apache-2.0 */
/* Product owners -> Diagnostics cache -> actual adapter/codec in a real kernel.
 * Synthetic map/vehicle metadata, host clock and console are test boundaries. */
#include "engine.h"
#include "lattice_memory.h"
#include <xenova/telemetry.h>
#include <zephyr/ztest.h>
#include <zephyr/kernel/mm.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
extern const struct lp_config lp_selected_config;
extern const struct lp_assets lp_selected_assets;
static struct drive_engine engine;
static struct xt_observation observation;
static struct xt_publisher publisher;
static struct xt_message message;
static char sample[37],snapshot_id[37];
static void emit(enum xt_kind kind,unsigned part){
 zassert_ok(xt_sample_id(&publisher,sample));
 zassert_ok(xt_encode(&publisher,kind,&observation,observation.end_ms,sample,snapshot_id,part,&message));
 printf("MEMORY_WIRE %s\n",message.bytes);
}
static void collect(void){k_sleep(K_MSEC(230));zassert_ok(xt_observe(&observation,false));}
static void *setup(void){
 de_init(&engine,&lp_selected_config,&lp_selected_assets);
 zassert_ok(drive_lattice_memory_register(&engine));
 zassert_equal(drive_lattice_memory_register(&engine),-EALREADY);
 struct xt_identity id={.vm_id="f1000000-0000-4000-8000-000000000001",
  .boot_id="f2000000-0000-4000-8000-000000000001",
  .publisher_id="f3000000-0000-4000-8000-000000000001"};
 struct xt_inventory info={.os_version="4.4.0",.build_id="app-memory-native",
  .profile="diagnostic_exercise",.task_id="memory-test",.interface_name="loopback",
  .diagnostics=true,.threads=true,.heap=true,.publisher=true};
 zassert_ok(xt_init(&publisher,&id,&info));zassert_ok(xt_sample_id(&publisher,snapshot_id));
 return NULL;
}
ZTEST(memory,test_real_pool_exhaustion_busy_release_and_codec){
 collect();zassert_true(observation.resources.app_registered);
 zassert_equal(observation.threads_rc,-EAGAIN);zassert_equal(observation.rows,0);
 zassert_equal(observation.resources.app[0].used,0);emit(XT_APP_MEMORY,0);
 struct dw_trust trust={0};zassert_ok(dw_pool_source(&engine.maps,&trust));
 struct dw_handle h[4];struct dw_snapshot *payload;
 for(unsigned i=0;i<DW_MAP_SLOTS;i++)zassert_ok(dw_reserve(&engine.maps,&h[i],&payload));
 zassert_equal(dw_reserve(&engine.maps,&h[3],&payload),DW_NO_SPACE);
 collect();struct xdr_app_region full=observation.resources.app[0];
 zassert_ok(full.rc);zassert_equal(full.used,full.capacity);zassert_equal(full.peak,full.capacity);
 zassert_equal(full.exhausted,1);emit(XT_APP_MEMORY,0);
 /* Hold only the test-owned metadata lock across the actual 200ms collector. */
 zassert_false(atomic_flag_test_and_set(&engine.maps.lock));
 collect();zassert_equal(observation.resources.app[0].rc,-EBUSY);emit(XT_APP_MEMORY,0);
 atomic_flag_clear(&engine.maps.lock);
 for(unsigned i=0;i<DW_MAP_SLOTS;i++)zassert_ok(dw_abort(&engine.maps,h[i]));
 collect();zassert_ok(observation.resources.app[0].rc);zassert_equal(observation.resources.app[0].used,0);
 zassert_equal(observation.resources.app[0].peak,full.capacity);
 zassert_equal(observation.resources.app[0].exhausted,1);
 for(unsigned i=0;i<observation.resources.app_count;i++)emit(XT_APP_MEMORY,i);
 dw_vehicle vehicle={.sequence=1,.source_sample=1,.source_us=1,.pi_sample_us=1};
 zassert_ok(dw_odom_add(&engine.odometry,&vehicle));collect();
 zassert_true(observation.resources.app[3].used>0);
 zassert_ok(dw_odom_reset(&engine.odometry));collect();
 zassert_equal(observation.resources.app[3].used,0);zassert_true(observation.resources.app[3].peak>0);
 emit(XT_APP_MEMORY,3);
 printf("MEMORY_COST samples=%llu total_us=%llu max_us=%llu cache_bytes=%zu\n",
  observation.resources.sample,observation.resources.total_collect_us,observation.resources.max_collect_us,sizeof(struct xdr_snapshot));
}
static struct k_thread measured,oversized;
K_THREAD_STACK_DEFINE(measured_stack,8192);
K_THREAD_STACK_DEFINE(oversized_stack,20000);
K_SEM_DEFINE(ready,0,1);
K_SEM_DEFINE(grow,0,1);
K_SEM_DEFINE(grown,0,1);
static __attribute__((noinline)) void touch_stack(void){
 volatile unsigned char bytes[1536];
 for(unsigned i=0;i<sizeof(bytes);i++)bytes[i]=(unsigned char)i;
 zassert_equal(bytes[21],21);
}
static void stack_owner(void *a,void *b,void *c){
 ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);k_sem_give(&ready);
 k_sem_take(&grow,K_FOREVER);touch_stack();k_sem_give(&grown);k_sleep(K_FOREVER);
}
static size_t unused(void){
 for(size_t i=0;i<observation.rows;i++)if(!strcmp(observation.threads[i].name,"memory-stack")){
  if(IS_ENABLED(CONFIG_ARCH_POSIX))zassert_equal(observation.threads[i].stack_rc,-ENOTSUP);
  else zassert_ok(observation.threads[i].stack_rc);return observation.threads[i].unused_bytes;
 }
 zassert_true(false,"measured thread missing");return 0;
}
ZTEST(memory,test_stack_growth_is_explicit_high_water_not_current){
 k_thread_create(&measured,measured_stack,K_THREAD_STACK_SIZEOF(measured_stack),stack_owner,NULL,NULL,NULL,5,0,K_FOREVER);
 k_thread_name_set(&measured,"memory-stack");k_thread_start(&measured);
 zassert_ok(k_sem_take(&ready,K_SECONDS(1)));zassert_ok(xt_observe(&observation,true));
 size_t before=unused();k_sem_give(&grow);zassert_ok(k_sem_take(&grown,K_SECONDS(1)));
 zassert_ok(xt_observe(&observation,true));size_t after=unused();
 if(!IS_ENABLED(CONFIG_ARCH_POSIX))zassert_true(after+1024<before,"real stack fill scan must observe growth");
 else printf("MEMORY_STACK_UNAVAILABLE POSIX uses pthread stacks; ARM scan remains separately tested\n");
 for(unsigned part=0;part<(observation.rows+3)/4;part++)emit(XT_THREADS,part);
 zassert_ok(xt_observe(&observation,true));zassert_true(unused()<=after);
 k_thread_abort(&measured);zassert_ok(k_thread_join(&measured,K_MSEC(200)));
 k_thread_create(&oversized,oversized_stack,K_THREAD_STACK_SIZEOF(oversized_stack),stack_owner,NULL,NULL,NULL,5,0,K_FOREVER);
 k_thread_name_set(&oversized,"memory-too-large");
 zassert_ok(xt_observe(&observation,true));bool found=false;
 for(size_t i=0;i<observation.rows;i++)if(!strcmp(observation.threads[i].name,"memory-too-large")){
  zassert_equal(observation.threads[i].stack_rc,-E2BIG);found=true;
 }
 zassert_true(found);
 for(unsigned part=0;part<(observation.rows+3)/4;part++)emit(XT_THREADS,part);
 k_thread_abort(&oversized);zassert_ok(k_thread_join(&oversized,K_MSEC(200)));
 zassert_ok(xt_observe(&observation,false));zassert_equal(observation.rows,0);
 printf("MEMORY_STACK before_unused=%zu after_unused=%zu scan_us=%llu\n",before,after,observation.elapsed_us);
}
/* Physical page accounting and fixed-pool occupancy are different scopes.
 * Only the existing fixed MMU/QEMU profile executes anonymous page allocation. */
ZTEST(memory,test_managed_ram_scope_internal_pool_and_page_allocations){
#if defined(CONFIG_MMU) && defined(CONFIG_KERNEL_VM_SUPPORT)
 const uint64_t total=(uint64_t)CONFIG_SRAM_SIZE*1024;
 const size_t pages=2*(size_t)CONFIG_MMU_PAGE_SIZE;
 zassert_equal(CONFIG_MMU_PAGE_SIZE,4096);zassert_equal(total%CONFIG_MMU_PAGE_SIZE,0);
 collect();size_t before=k_mem_free_get();zassert_true(before<=total);zassert_true(before>=pages);
 void *heap=k_malloc(512);zassert_not_null(heap);memset(heap,0x5a,512);
 size_t heap_free=k_mem_free_get();k_free(heap);size_t heap_released=k_mem_free_get();
 zassert_equal(heap_free,before);zassert_equal(heap_released,before);
 struct dw_memory initial,occupied,released;zassert_ok(de_memory_read(&engine,0,&initial));
 struct dw_trust trust={0};zassert_ok(dw_pool_source(&engine.maps,&trust));
 struct dw_handle handle;struct dw_snapshot *payload;
 zassert_ok(dw_reserve(&engine.maps,&handle,&payload));memset(payload,0,sizeof(*payload));
 collect();zassert_ok(de_memory_read(&engine,0,&occupied));
 zassert_equal(occupied.used,initial.used+sizeof(*payload));zassert_true(occupied.peak>=occupied.used);
 zassert_equal(observation.resources.app[0].used,occupied.used);
 zassert_equal(observation.resources.app[0].peak,occupied.peak);emit(XT_APP_MEMORY,0);
 size_t pool_free=k_mem_free_get();zassert_ok(dw_abort(&engine.maps,handle));
 collect();zassert_ok(de_memory_read(&engine,0,&released));
 zassert_equal(released.used,initial.used);zassert_equal(released.peak,occupied.peak);
 zassert_equal(observation.resources.app[0].used,released.used);emit(XT_APP_MEMORY,0);
 zassert_equal(pool_free,before);zassert_equal(k_mem_free_get(),before);
 volatile unsigned char *mapping=k_mem_map(pages,K_MEM_PERM_RW);zassert_not_null(mapping);
 mapping[0]=0x12;mapping[CONFIG_MMU_PAGE_SIZE]=0x34;mapping[pages-1]=0x56;
 bool readable=mapping[0]==0x12&&mapping[CONFIG_MMU_PAGE_SIZE]==0x34&&mapping[pages-1]==0x56;
 size_t mapped_free=k_mem_free_get();k_mem_unmap((void *)mapping,pages);
 size_t returned=k_mem_free_get();zassert_true(readable);
 zassert_equal(before-mapped_free,8192);zassert_equal(returned,before);
 uint64_t acquired_before=total-before,acquired_mapped=total-mapped_free;
 zassert_equal(acquired_mapped,acquired_before+8192);zassert_equal(total-returned,acquired_before);
 printf("MANAGED_RAM total=%llu free_before=%zu heap_free=%zu pool_free=%zu mapped_free=%zu returned=%zu acquired_before=%llu acquired_mapped=%llu pool_used=%llu pool_released=%llu pool_peak=%llu\n",
  (unsigned long long)total,before,heap_free,pool_free,mapped_free,returned,
  (unsigned long long)acquired_before,(unsigned long long)acquired_mapped,
  (unsigned long long)occupied.used,(unsigned long long)released.used,(unsigned long long)released.peak);
#else
 collect();zassert_equal(observation.resources.firmware_rc,-ENOTSUP);
 zassert_equal(observation.resources.page_rc,-ENOTSUP);
 bool ratio_available=!observation.resources.firmware_rc&&!observation.resources.page_rc&&
  observation.resources.ram_region_bytes&&observation.resources.page_free_bytes<=observation.resources.ram_region_bytes;
 zassert_false(ratio_available);
 printf("MANAGED_RAM_UNSUPPORTED firmware_rc=%d page_rc=%d derived=unavailable\n",
  observation.resources.firmware_rc,observation.resources.page_rc);
#endif
 /* QEMU is not BOARD_XENVM: do not relabel direct allocator evidence as a
  * supported Xen producer. Native also keeps unavailable values null. */
 emit(XT_MEMORY,0);
}
ZTEST_SUITE(memory,NULL,setup,NULL,NULL,NULL);
