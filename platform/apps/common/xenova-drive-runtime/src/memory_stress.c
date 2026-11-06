/* SPDX-License-Identifier: Apache-2.0 */
#include <xenova/memory_stress.h>
#include <zephyr/kernel.h>

#define MEMORY_STRESS_CHUNK_SIZE (64U * 1024U)
#define MEMORY_STRESS_MAX_MIB 8U
#define MEMORY_STRESS_MAX_CHUNKS (MEMORY_STRESS_MAX_MIB * 1024U / 64U)

K_THREAD_STACK_DEFINE(memory_stress_stack,2048);
K_SEM_DEFINE(memory_stress_start_sem,0,1);
K_SEM_DEFINE(memory_stress_stop_sem,0,1);
K_SEM_DEFINE(memory_stress_done_sem,0,1);
K_MUTEX_DEFINE(memory_stress_lock);
static struct k_thread memory_stress_thread;
static void *chunks[MEMORY_STRESS_MAX_CHUNKS];
static uint32_t requested_mib,duration_s,chunk_count;
static enum drive_memory_stress_state state;
static int stress_error;
static bool stop_requested,thread_started;

static void release_chunks(void)
{
	for(;;){
		k_mutex_lock(&memory_stress_lock,K_FOREVER);
		if(!chunk_count){k_mutex_unlock(&memory_stress_lock);break;}
		void *block=chunks[--chunk_count];chunks[chunk_count]=NULL;
		k_mutex_unlock(&memory_stress_lock);k_free(block);
	}
}

static void touch_chunk(uint8_t *chunk)
{
	for(size_t offset=0;offset<MEMORY_STRESS_CHUNK_SIZE;offset+=4096U)chunk[offset]=(uint8_t)(offset>>12);
	chunk[MEMORY_STRESS_CHUNK_SIZE-1]=0xa5;
}

static void memory_stress_worker(void *a,void *b,void *c)
{
	ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);
	for(;;){
		k_sem_take(&memory_stress_start_sem,K_FOREVER);
		k_mutex_lock(&memory_stress_lock,K_FOREVER);
		uint32_t target_chunks=requested_mib*1024U/64U,hold_s=duration_s;
		k_mutex_unlock(&memory_stress_lock);int rc=0;
		for(uint32_t i=0;i<target_chunks;i++){
			k_mutex_lock(&memory_stress_lock,K_FOREVER);bool stopping=stop_requested;k_mutex_unlock(&memory_stress_lock);
			if(stopping){rc=-ECANCELED;break;}
			void *block=k_malloc(MEMORY_STRESS_CHUNK_SIZE);
			if(!block){rc=-ENOMEM;break;}
			touch_chunk(block);k_mutex_lock(&memory_stress_lock,K_FOREVER);
			chunks[chunk_count++]=block;k_mutex_unlock(&memory_stress_lock);
		}
		if(!rc&&k_sem_take(&memory_stress_stop_sem,K_SECONDS(hold_s))==0)rc=-ECANCELED;
		release_chunks();
		k_mutex_lock(&memory_stress_lock,K_FOREVER);
		stress_error=rc;state=rc==0?DRIVE_MEMORY_STRESS_SUCCEEDED:(rc==-ECANCELED?DRIVE_MEMORY_STRESS_IDLE:DRIVE_MEMORY_STRESS_FAILED);
		stop_requested=false;k_mutex_unlock(&memory_stress_lock);k_sem_give(&memory_stress_done_sem);
	}
}

static void ensure_thread(void)
{
	if(thread_started)return;
	k_thread_create(&memory_stress_thread,memory_stress_stack,K_THREAD_STACK_SIZEOF(memory_stress_stack),memory_stress_worker,NULL,NULL,NULL,11,0,K_NO_WAIT);
	k_thread_name_set(&memory_stress_thread,"memory-stress");thread_started=true;
}

int drive_memory_stress_start(uint32_t memory_mib,uint32_t seconds)
{
	if(memory_mib<1||memory_mib>MEMORY_STRESS_MAX_MIB||seconds<1||seconds>120)return -EINVAL;
	k_mutex_lock(&memory_stress_lock,K_FOREVER);
	if(state==DRIVE_MEMORY_STRESS_RUNNING){k_mutex_unlock(&memory_stress_lock);return -EBUSY;}
	ensure_thread();requested_mib=memory_mib;duration_s=seconds;stress_error=0;stop_requested=false;state=DRIVE_MEMORY_STRESS_RUNNING;
	k_sem_reset(&memory_stress_stop_sem);k_sem_reset(&memory_stress_done_sem);k_mutex_unlock(&memory_stress_lock);
	k_sem_give(&memory_stress_start_sem);return 0;
}

void drive_memory_stress_status_get(struct drive_memory_stress_status *out)
{
	if(!out)return;k_mutex_lock(&memory_stress_lock,K_FOREVER);
	*out=(struct drive_memory_stress_status){state,requested_mib,chunk_count*64U,stress_error};k_mutex_unlock(&memory_stress_lock);
}

void drive_memory_stress_stop(void)
{
	k_mutex_lock(&memory_stress_lock,K_FOREVER);
	bool running=state==DRIVE_MEMORY_STRESS_RUNNING;if(running)stop_requested=true;k_mutex_unlock(&memory_stress_lock);
	if(!running)return;k_sem_give(&memory_stress_stop_sem);k_sem_take(&memory_stress_done_sem,K_FOREVER);
}
