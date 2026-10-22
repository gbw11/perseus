/* SPDX-License-Identifier: Apache-2.0 */
#ifndef XENOVA_GRANT_POOL_H
#define XENOVA_GRANT_POOL_H
#include <stdbool.h>
#include <stdint.h>
#define XGP_FIRST 8
#define XGP_COUNT 32
#define XGP_PERMIT 1
#define XGP_READONLY 4
#define XGP_READING 8
#define XGP_WRITING 16
struct xgp_entry { uint16_t flags,domid; uint32_t frame; };
struct xgp_pool { struct xgp_entry *table; bool held[XGP_COUNT]; };
int xgp_share(struct xgp_pool *,uint16_t backend,uint32_t gfn,bool readonly,uint32_t *ref);
int xgp_revoke(struct xgp_pool *,uint32_t ref);
#endif
