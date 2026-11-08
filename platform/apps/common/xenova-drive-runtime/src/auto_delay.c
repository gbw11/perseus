/* SPDX-License-Identifier: Apache-2.0 */
#include <xenova/auto_delay.h>
#include <zephyr/kernel.h>
#include <errno.h>
static struct k_spinlock lock;
static uint32_t configured_delay_ms,consecutive_misses;
static int64_t expires_at_ms;
static bool unhealthy;
static bool active_locked(int64_t now){if(!configured_delay_ms||now>=expires_at_ms){configured_delay_ms=0;expires_at_ms=0;return false;}return true;}
int drive_auto_delay_start(uint32_t delay,uint32_t duration){
 if(!delay||delay>DRIVE_AUTO_DELAY_MAX_MS||!duration||duration>DRIVE_AUTO_DELAY_MAX_DURATION_S)return -EINVAL;
 k_spinlock_key_t key=k_spin_lock(&lock);if(active_locked(k_uptime_get())){k_spin_unlock(&lock,key);return -EBUSY;}
 configured_delay_ms=delay;expires_at_ms=k_uptime_get()+(int64_t)duration*1000;consecutive_misses=0;unhealthy=false;k_spin_unlock(&lock,key);return 0;
}
void drive_auto_delay_stop(void){k_spinlock_key_t key=k_spin_lock(&lock);configured_delay_ms=0;expires_at_ms=0;consecutive_misses=0;unhealthy=false;k_spin_unlock(&lock,key);}
bool drive_auto_delay_active(void){k_spinlock_key_t key=k_spin_lock(&lock);bool value=active_locked(k_uptime_get());k_spin_unlock(&lock,key);return value;}
void drive_auto_delay_apply(void){uint32_t delay=0;k_spinlock_key_t key=k_spin_lock(&lock);if(active_locked(k_uptime_get()))delay=configured_delay_ms;k_spin_unlock(&lock,key);if(delay)k_busy_wait(delay*1000U);}
bool drive_auto_delay_observe_deadline(bool missed){k_spinlock_key_t key=k_spin_lock(&lock);if(missed){if(consecutive_misses<DRIVE_AUTO_DEADLINE_MISS_LIMIT)consecutive_misses++;unhealthy=consecutive_misses>=DRIVE_AUTO_DEADLINE_MISS_LIMIT;}else{consecutive_misses=0;unhealthy=false;}bool value=unhealthy;k_spin_unlock(&lock,key);return value;}
void drive_auto_delay_status_get(struct drive_auto_delay_status *out){if(!out)return;k_spinlock_key_t key=k_spin_lock(&lock);bool active=active_locked(k_uptime_get());*out=(struct drive_auto_delay_status){configured_delay_ms,consecutive_misses,expires_at_ms,active,unhealthy};k_spin_unlock(&lock,key);}
