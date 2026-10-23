/* SPDX-License-Identifier: Apache-2.0 */
#ifndef XENOVA_RUNTIME_H
#define XENOVA_RUNTIME_H
#include <xenova/xenstore.h>
#include <zephyr/kernel.h>
/* One Store session per domain. No caller may retain the client after release.
 * Work/quiesce callbacks are never invoked while holding this lock. */
int xsr_open(void);
struct xs_client *xsr_acquire(int64_t deadline);
void xsr_release(void);
int xsr_close(int64_t deadline);
/* Body uses the supplied client under the session lock; do not nest xsr_* calls. */
int xsr_transaction(int timeout_ms, xs_transaction_fn body, void *context);
int xsr_watch_next(uint32_t watch, int timeout_ms);
int xsr_read(const char *path, char *value, size_t capacity, int timeout_ms);
int xsr_write(const char *path, const char *value, int timeout_ms);
/* Limited owned grant table. Mutually exclusive with Zephyr XEN_GRANT_TABLE. */
int xgrant_init(void);
int xgrant_share(uint16_t backend, void *page, bool readonly, uint32_t *reference);
int xgrant_revoke(uint32_t reference);
#endif
