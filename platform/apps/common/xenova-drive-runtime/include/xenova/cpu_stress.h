/* SPDX-License-Identifier: Apache-2.0 */
#ifndef XENOVA_CPU_STRESS_H
#define XENOVA_CPU_STRESS_H
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>

enum drive_cpu_stress_state { DRIVE_CPU_STRESS_IDLE, DRIVE_CPU_STRESS_RUNNING,
	DRIVE_CPU_STRESS_SUCCEEDED, DRIVE_CPU_STRESS_FAILED };
struct drive_cpu_stress_status {
	enum drive_cpu_stress_state state;
	uint32_t load_percent;
	int error;
};
#if defined(CONFIG_XENOVA_RESOURCE_STRESS_INJECTION)
int drive_cpu_stress_start(uint32_t workers,uint32_t load_percent,uint32_t duration_s);
void drive_cpu_stress_status_get(struct drive_cpu_stress_status *out);
void drive_cpu_stress_stop(void);
#else
static inline int drive_cpu_stress_start(uint32_t a,uint32_t b,uint32_t c){(void)a;(void)b;(void)c;return -ENOTSUP;}
static inline void drive_cpu_stress_status_get(struct drive_cpu_stress_status *o){if(o)*o=(struct drive_cpu_stress_status){0};}
static inline void drive_cpu_stress_stop(void){}
#endif
#endif
