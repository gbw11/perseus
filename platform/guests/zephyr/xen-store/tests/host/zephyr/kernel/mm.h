/* SPDX-License-Identifier: Apache-2.0 */
#include <stdint.h>
#include <stddef.h>
#define K_MEM_CACHE_WB 1
#define K_MEM_PERM_RW 2
void k_mem_map_phys_bare(uint8_t **,uintptr_t,size_t,int);
void k_mem_unmap_phys_bare(void *,size_t);
