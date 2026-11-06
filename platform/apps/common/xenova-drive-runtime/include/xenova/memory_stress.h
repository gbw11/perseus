/* SPDX-License-Identifier: Apache-2.0 */
#ifndef XENOVA_MEMORY_STRESS_H
#define XENOVA_MEMORY_STRESS_H
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>

enum drive_memory_stress_state { DRIVE_MEMORY_STRESS_IDLE, DRIVE_MEMORY_STRESS_RUNNING,
	DRIVE_MEMORY_STRESS_SUCCEEDED, DRIVE_MEMORY_STRESS_FAILED };
struct drive_memory_stress_status {
	enum drive_memory_stress_state state;
	uint32_t requested_mib, allocated_kib;
	int error;
};
#if defined(CONFIG_XENOVA_RESOURCE_STRESS_INJECTION)
int drive_memory_stress_start(uint32_t memory_mib,uint32_t duration_s);
void drive_memory_stress_status_get(struct drive_memory_stress_status *out);
void drive_memory_stress_stop(void);
#else
static inline int drive_memory_stress_start(uint32_t a,uint32_t b){(void)a;(void)b;return -ENOTSUP;}
static inline void drive_memory_stress_status_get(struct drive_memory_stress_status *o){if(o)*o=(struct drive_memory_stress_status){0};}
static inline void drive_memory_stress_stop(void){}
#endif
#endif
