/* SPDX-License-Identifier: Apache-2.0 */
/** Zephyr ARM64 transport. Call from thread context after kernel initialization. */
#ifndef XENOVA_XENSTORE_ZEPHYR_H
#define XENOVA_XENSTORE_ZEPHYR_H
#include <xenova/xenstore_link.h>
#include <zephyr/kernel.h>
struct xs_zephyr {
	struct xs_link link;
	struct k_sem wake;
};
int xs_zephyr_open(struct xs_zephyr *);
int xs_zephyr_close(struct xs_zephyr *, int timeout_ms);
#endif
