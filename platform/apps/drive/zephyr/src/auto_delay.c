/* SPDX-License-Identifier: Apache-2.0 */
#include "auto_delay.h"

#include <zephyr/kernel.h>
#include <errno.h>

static struct k_spinlock lock;
static uint32_t configured_delay_ms;
static uint32_t consecutive_misses;
static int64_t expires_at_ms;
static bool unhealthy;

static bool active_locked(int64_t now)
{
	if (!configured_delay_ms || now >= expires_at_ms) {
		configured_delay_ms = 0;
		expires_at_ms = 0;
		return false;
	}
	return true;
}

int drive_auto_delay_start(uint32_t delay_ms, uint32_t duration_s)
{
	if (!delay_ms || delay_ms > DRIVE_AUTO_DELAY_MAX_MS || !duration_s ||
	    duration_s > DRIVE_AUTO_DELAY_MAX_DURATION_S) {
		return -EINVAL;
	}

	k_spinlock_key_t key = k_spin_lock(&lock);
	if (active_locked(k_uptime_get())) {
		k_spin_unlock(&lock, key);
		return -EBUSY;
	}
	configured_delay_ms = delay_ms;
	expires_at_ms = k_uptime_get() + (int64_t)duration_s * 1000;
	consecutive_misses = 0;
	unhealthy = false;
	k_spin_unlock(&lock, key);
	return 0;
}

void drive_auto_delay_stop(void)
{
	k_spinlock_key_t key = k_spin_lock(&lock);
	configured_delay_ms = 0;
	expires_at_ms = 0;
	consecutive_misses = 0;
	unhealthy = false;
	k_spin_unlock(&lock, key);
}

bool drive_auto_delay_active(void)
{
	k_spinlock_key_t key = k_spin_lock(&lock);
	bool active = active_locked(k_uptime_get());
	k_spin_unlock(&lock, key);
	return active;
}

void drive_auto_delay_apply(void)
{
	uint32_t delay_ms = 0;
	k_spinlock_key_t key = k_spin_lock(&lock);
	if (active_locked(k_uptime_get())) {
		delay_ms = configured_delay_ms;
	}
	k_spin_unlock(&lock, key);
	if (delay_ms) {
		k_busy_wait(delay_ms * 1000U);
	}
}

bool drive_auto_delay_observe_deadline(bool missed)
{
	k_spinlock_key_t key = k_spin_lock(&lock);
	if (missed) {
		if (consecutive_misses < DRIVE_AUTO_DEADLINE_MISS_LIMIT) {
			++consecutive_misses;
		}
		unhealthy = consecutive_misses >= DRIVE_AUTO_DEADLINE_MISS_LIMIT;
	} else {
		consecutive_misses = 0;
		unhealthy = false;
	}
	bool result = unhealthy;
	k_spin_unlock(&lock, key);
	return result;
}

void drive_auto_delay_status_get(struct drive_auto_delay_status *out)
{
	if (!out) {
		return;
	}
	k_spinlock_key_t key = k_spin_lock(&lock);
	bool active = active_locked(k_uptime_get());
	*out = (struct drive_auto_delay_status){
		.delay_ms = configured_delay_ms,
		.consecutive_deadline_misses = consecutive_misses,
		.expires_at_ms = expires_at_ms,
		.active = active,
		.unhealthy = unhealthy,
	};
	k_spin_unlock(&lock, key);
}
