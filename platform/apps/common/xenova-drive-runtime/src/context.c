/* SPDX-License-Identifier: Apache-2.0 */
/* Reuses the runtime Store receiver. A failed read revokes availability;
 * previously accepted base execution/credentials may never silently change. */
#include <xenova/drive_context.h>
#include <zephyr/sys/atomic.h>
#include <errno.h>
#include <string.h>
K_MUTEX_DEFINE(context_lock);
K_MUTEX_DEFINE(context_lifecycle);
K_SEM_DEFINE(context_wake,0,1);
K_THREAD_STACK_DEFINE(context_stack,8192);
static struct k_thread thread;
static struct xt_bootstrap current,first,incoming;
static xt_bootstrap_loader load;
static bool attempted,started,joined,available,identified;
static atomic_t stop;
static int64_t observed;
static struct xd_context_status status;

static void work(void *a,void *b,void *c)
{
 ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);
 while(!atomic_get(&stop)){
  memset(&incoming,0,sizeof(incoming));
  int rc=load(&incoming,k_uptime_get()+50,"xenova-drive");
  if(!rc&&(strcmp(incoming.drive_profile,"warm-drive-1")||!incoming.auto_instance_id[0]||!incoming.manual_instance_id[0]))rc=-EPROTO;
  if(!rc&&identified&&(memcmp(&first.identity,&incoming.identity,sizeof(first.identity))||
     strcmp(first.username,incoming.username)||strcmp(first.password,incoming.password)||
     strcmp(first.broker_ipv4,incoming.broker_ipv4)||first.port!=incoming.port||
     strcmp(first.world_broker_ipv4,incoming.world_broker_ipv4)||first.world_port!=incoming.world_port||
     strcmp(first.world_username,incoming.world_username)||strcmp(first.world_password,incoming.world_password)||
     strcmp(first.world_profile,incoming.world_profile)))rc=-EXDEV;
  k_mutex_lock(&context_lock,K_FOREVER);
  status.loads++;
  if(rc){
   status.failures++;
   if(!status.first_error)status.first_error=rc;
  }
  status.last_error=rc;
  available=!rc&&!atomic_get(&stop);
  if(available){current=incoming;observed=k_uptime_get();if(!identified){first=incoming;identified=true;}}
  k_mutex_unlock(&context_lock);
  if(!atomic_get(&stop))k_sem_take(&context_wake,K_MSEC(100));
 }
 k_mutex_lock(&context_lock,K_FOREVER);available=false;k_mutex_unlock(&context_lock);
 memset(&incoming,0,sizeof(incoming));
}
int xd_context_start(xt_bootstrap_loader loader)
{
 if(!loader)return -EINVAL;
 if(k_mutex_lock(&context_lifecycle,K_NO_WAIT))return -EBUSY;
 int rc=0;
 if(atomic_get(&stop))rc=-ESHUTDOWN;
 else if(attempted)rc=started?0:-ENOMEM;
 else{
  attempted=true;load=loader;
  started=k_thread_create(&thread,context_stack,K_THREAD_STACK_SIZEOF(context_stack),work,NULL,NULL,NULL,9,0,K_NO_WAIT)!=NULL;
  if(!started)rc=-ENOMEM;else k_thread_name_set(&thread,"drive-context");
 }
 k_mutex_unlock(&context_lifecycle);return rc;
}
int xd_context_get(struct xt_bootstrap *out)
{
 if(!out)return -EINVAL;
 if(k_mutex_lock(&context_lock,K_NO_WAIT))return -EBUSY;
 int rc=available&&!atomic_get(&stop)&&k_uptime_get()-observed<=300?0:-EAGAIN;
 if(!rc)*out=current;else memset(out,0,sizeof(*out));
 k_mutex_unlock(&context_lock);return rc;
}
int xd_context_status_get(struct xd_context_status *out)
{
 if(k_mutex_lock(&context_lock,K_NO_WAIT))return -EBUSY;
 *out=status;
 out->available=available&&!atomic_get(&stop)&&k_uptime_get()-observed<=300;
 k_mutex_unlock(&context_lock);
 return 0;
}
int xd_context_join(int64_t end)
{
 atomic_set(&stop,1);k_sem_give(&context_wake);
 int64_t left=end-k_uptime_get();
 if(k_mutex_lock(&context_lifecycle,left>0?K_MSEC(left):K_NO_WAIT))return -ETIMEDOUT;
 int rc=0;
 if(started&&!joined){left=end-k_uptime_get();rc=k_thread_join(&thread,left>0?K_MSEC(left):K_NO_WAIT);if(!rc)joined=true;}
 if(!rc){memset(&first,0,sizeof(first));memset(&current,0,sizeof(current));}
 k_mutex_unlock(&context_lifecycle);return rc?-ETIMEDOUT:0;
}
