/* SPDX-License-Identifier: Apache-2.0 */
/* Host-only kernel boundary; never used by the xenvm target build. */
#ifndef G1_HOST_KERNEL_H
#define G1_HOST_KERNEL_H
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
struct k_sem { unsigned int count; };
struct k_mutex { int unused; };
#define K_MUTEX_DEFINE(n) struct k_mutex n
#define K_FOREVER (-1)
#define K_MSEC(n) (n)
#define ARG_UNUSED(x) (void)(x)
#define CONFIG_ARM64_PA_BITS 32
int k_mutex_lock(struct k_mutex *, int);
int k_mutex_unlock(struct k_mutex *);
int k_sem_init(struct k_sem *, unsigned int, unsigned int);
void k_sem_give(struct k_sem *);
int k_sem_take(struct k_sem *, int64_t);
int64_t k_uptime_get(void);
int k_is_in_isr(void);
uint64_t k_cycle_get_64(void);
void k_sleep(int);
#endif
