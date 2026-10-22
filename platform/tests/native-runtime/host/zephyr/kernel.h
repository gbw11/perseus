/* SPDX-License-Identifier: Apache-2.0 */
/* Native-only kernel boundary. Production runtime.c is compiled unchanged. */
#ifndef XENOVA_RUNTIME_NATIVE_KERNEL_H
#define XENOVA_RUNTIME_NATIVE_KERNEL_H
#include <stdint.h>
#include <stdbool.h>
#include <pthread.h>
struct k_mutex { pthread_mutex_t mutex; };
struct k_sem { int unused; };
#define K_MUTEX_DEFINE(n) struct k_mutex n={PTHREAD_MUTEX_INITIALIZER}
#define K_MSEC(n) (n)
#define K_FOREVER (-1)
#define ARG_UNUSED(x) (void)(x)
#define MAX(a,b) ((a)>(b)?(a):(b))
int k_mutex_lock(struct k_mutex *,int64_t);
int k_mutex_unlock(struct k_mutex *);
int64_t k_uptime_get(void);
int k_is_in_isr(void);
#endif
