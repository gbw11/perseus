/* SPDX-License-Identifier: Apache-2.0 */
#ifndef XENOVA_DRIVE_AUTO_DELAY_H
#define XENOVA_DRIVE_AUTO_DELAY_H
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
#define DRIVE_AUTO_DELAY_MAX_MS 100U
#define DRIVE_AUTO_DELAY_MAX_DURATION_S 120U
#define DRIVE_AUTO_DEADLINE_MISS_LIMIT 5U
struct drive_auto_delay_status {
 uint32_t delay_ms,consecutive_deadline_misses;int64_t expires_at_ms;bool active,unhealthy;
};
#if defined(CONFIG_XENOVA_AUTO_DELAY_INJECTION)
int drive_auto_delay_start(uint32_t,uint32_t);
void drive_auto_delay_stop(void);
bool drive_auto_delay_active(void);
void drive_auto_delay_apply(void);
bool drive_auto_delay_observe_deadline(bool);
void drive_auto_delay_status_get(struct drive_auto_delay_status *);
#else
static inline int drive_auto_delay_start(uint32_t a,uint32_t b){(void)a;(void)b;return -ENOTSUP;}
static inline void drive_auto_delay_stop(void){}
static inline bool drive_auto_delay_active(void){return false;}
static inline void drive_auto_delay_apply(void){}
static inline bool drive_auto_delay_observe_deadline(bool a){(void)a;return false;}
static inline void drive_auto_delay_status_get(struct drive_auto_delay_status *o){if(o)*o=(struct drive_auto_delay_status){0};}
#endif
#endif
