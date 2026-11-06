/* SPDX-License-Identifier: Apache-2.0 */
#include <xenova/cpu_stress.h>
#include <zephyr/kernel.h>

#define CPU_STRESS_PERIOD_US 10000U

K_THREAD_STACK_DEFINE(cpu_stress_stack,2048);
K_SEM_DEFINE(cpu_stress_start_sem,0,1);
K_SEM_DEFINE(cpu_stress_done_sem,0,1);
K_MUTEX_DEFINE(cpu_stress_lock);
static struct k_thread cpu_stress_thread;
static uint32_t requested_load_percent,duration_s;
static enum drive_cpu_stress_state state;
static int stress_error;
static bool stop_requested,thread_started;

static bool stopping(void)
{
	k_mutex_lock(&cpu_stress_lock,K_FOREVER);
	bool value=stop_requested;
	k_mutex_unlock(&cpu_stress_lock);
	return value;
}

static void cpu_stress_worker(void *a,void *b,void *c)
{
	ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);
	for(;;){
		k_sem_take(&cpu_stress_start_sem,K_FOREVER);
		k_mutex_lock(&cpu_stress_lock,K_FOREVER);
		uint32_t load=requested_load_percent,seconds=duration_s;
		k_mutex_unlock(&cpu_stress_lock);
		int64_t deadline=k_uptime_get()+(int64_t)seconds*1000;
		uint32_t busy_us=CPU_STRESS_PERIOD_US*load/100U;
		uint32_t idle_us=CPU_STRESS_PERIOD_US-busy_us;
		int rc=0;
		while(k_uptime_get()<deadline){
			if(stopping()){rc=-ECANCELED;break;}
			if(busy_us)k_busy_wait(busy_us);
			if(idle_us)k_sleep(K_USEC(idle_us));
			else k_yield();
		}
		k_mutex_lock(&cpu_stress_lock,K_FOREVER);
		stress_error=rc;state=rc==0?DRIVE_CPU_STRESS_SUCCEEDED:DRIVE_CPU_STRESS_IDLE;
		stop_requested=false;k_mutex_unlock(&cpu_stress_lock);k_sem_give(&cpu_stress_done_sem);
	}
}

static void ensure_thread(void)
{
	if(thread_started)return;
	k_thread_create(&cpu_stress_thread,cpu_stress_stack,K_THREAD_STACK_SIZEOF(cpu_stress_stack),cpu_stress_worker,
		NULL,NULL,NULL,CONFIG_XENOVA_RESOURCE_STRESS_CPU_PRIORITY,0,K_NO_WAIT);
	k_thread_name_set(&cpu_stress_thread,"cpu-stress");thread_started=true;
}

int drive_cpu_stress_start(uint32_t workers,uint32_t load_percent,uint32_t seconds)
{
	if(workers!=1||load_percent<10||load_percent>100||seconds<1||seconds>120)return -EINVAL;
	k_mutex_lock(&cpu_stress_lock,K_FOREVER);
	if(state==DRIVE_CPU_STRESS_RUNNING){k_mutex_unlock(&cpu_stress_lock);return -EBUSY;}
	ensure_thread();requested_load_percent=load_percent;duration_s=seconds;stress_error=0;stop_requested=false;
	state=DRIVE_CPU_STRESS_RUNNING;k_sem_reset(&cpu_stress_done_sem);k_mutex_unlock(&cpu_stress_lock);
	k_sem_give(&cpu_stress_start_sem);return 0;
}

void drive_cpu_stress_status_get(struct drive_cpu_stress_status *out)
{
	if(!out)return;k_mutex_lock(&cpu_stress_lock,K_FOREVER);
	*out=(struct drive_cpu_stress_status){state,requested_load_percent,stress_error};k_mutex_unlock(&cpu_stress_lock);
}

void drive_cpu_stress_stop(void)
{
	k_mutex_lock(&cpu_stress_lock,K_FOREVER);
	bool running=state==DRIVE_CPU_STRESS_RUNNING;if(running)stop_requested=true;k_mutex_unlock(&cpu_stress_lock);
	if(running)k_sem_take(&cpu_stress_done_sem,K_FOREVER);
}
