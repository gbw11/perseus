/* SPDX-License-Identifier: Apache-2.0 */
/* Bounded, single-owner grant-v1 pool for this profile. No upstream allocator,
 * no unbounded wait; busy grants retain their pages and references. */
#include <xenova/runtime.h>
#include <xenova/grant_pool.h>
#include <zephyr/arch/arm64/hypercall.h>
#include <zephyr/xen/generic.h>
#include <zephyr/xen/public/grant_table.h>
#include <zephyr/xen/public/memory.h>
#include <zephyr/kernel/internal/mm.h>
#include <zephyr/kernel/mm.h>
#include <zephyr/sys/barrier.h>
#include <errno.h>
#include <string.h>
BUILD_ASSERT(!IS_ENABLED(CONFIG_XEN_GRANT_TABLE),"single grant table owner required");
static struct xgp_pool pool;
#define table pool.table
BUILD_ASSERT(sizeof(struct xgp_entry)==sizeof(grant_entry_v1_t),"grant-v1 ABI");
BUILD_ASSERT(XGP_PERMIT==GTF_permit_access&&XGP_READONLY==GTF_readonly&&
             XGP_READING==GTF_reading&&XGP_WRITING==GTF_writing,"grant flags ABI");
K_MUTEX_DEFINE(grant_lock);
int xgrant_init(void) {
 uintptr_t address=DT_REG_ADDR_BY_IDX(DT_INST(0,xen_xen),0);
 struct xen_add_to_physmap op={.domid=DOMID_SELF,.space=XENMAPSPACE_grant_table,
                              .idx=0,.gpfn=address>>12};
 if(table)return -EALREADY;
 /* Frozen Zephyr header has the operation but omits its single-u32 ABI. */
 struct { uint32_t version; } version={.version=1};
 int rc=HYPERVISOR_grant_table_op(GNTTABOP_set_version,&version,1);if(rc)return rc;
 rc=HYPERVISOR_memory_op(XENMEM_add_to_physmap,&op);if(rc)return rc;
 uint8_t *map=NULL;k_mem_map_phys_bare(&map,address,4096,K_MEM_CACHE_WB|K_MEM_PERM_RW);
 if(!map)return -ENOMEM;
 table=(struct xgp_entry *)map;return 0;
}
int xgrant_share(uint16_t backend,void *page,bool readonly,uint32_t *reference) {
 if(!table||!page||!reference||((uintptr_t)page&4095))return -EINVAL;
 /* Static page storage lives in the permanent RAM mapping; convert and check
  * both endpoints instead of casting a virtual pointer to a GFN. */
 uintptr_t physical=k_mem_phys_addr(page);
 if((physical&4095)||!IS_SRAM_ADDRESS(physical)||!IS_SRAM_ADDRESS(physical+4095)||
    (physical>>12)>UINT32_MAX)return -ERANGE;
 if(k_mutex_lock(&grant_lock,K_MSEC(20)))return -ETIMEDOUT;
 int rc=xgp_share(&pool,backend,physical>>12,readonly,reference);
 k_mutex_unlock(&grant_lock);return rc;
}
int xgrant_revoke_until(uint32_t ref,int64_t end) {
 int64_t left=end-k_uptime_get();if(left<=0)return -ETIMEDOUT;
 if(k_mutex_lock(&grant_lock,K_MSEC(MIN(20,left))))return -ETIMEDOUT;
 if(k_uptime_get()>=end){k_mutex_unlock(&grant_lock);return -ETIMEDOUT;}
 int rc=xgp_revoke(&pool,ref);k_mutex_unlock(&grant_lock);return rc;
}
int xgrant_revoke(uint32_t ref) { return xgrant_revoke_until(ref,k_uptime_get()+20); }
