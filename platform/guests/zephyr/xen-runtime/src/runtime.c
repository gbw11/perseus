/* SPDX-License-Identifier: Apache-2.0 */
/* Serialized Store access; short watch polling provides bounded fan-out. */
#include <xenova/runtime.h>
#include <xenova/xenstore_zephyr.h>
#include <errno.h>
#include <string.h>
K_MUTEX_DEFINE(session_lock);
static struct xs_zephyr session;
static bool opened;
static int lock_until(int64_t end) {
 int64_t left=end-k_uptime_get();
 if(left<=0)return -ETIMEDOUT;
 int rc=k_mutex_lock(&session_lock,K_MSEC(left));
 if(!rc&&k_uptime_get()>=end){k_mutex_unlock(&session_lock);return -ETIMEDOUT;}
 return rc;
}
int xsr_open(void) {
 if(k_is_in_isr())return -EWOULDBLOCK;
 int rc=lock_until(k_uptime_get()+1000);if(rc)return rc;
 if(!opened){rc=xs_zephyr_open(&session);if(!rc)opened=true;}
 k_mutex_unlock(&session_lock);return rc;
}
struct xs_client *xsr_acquire(int64_t end) {
 if(k_is_in_isr()||lock_until(end))return NULL;
 if(!opened){k_mutex_unlock(&session_lock);return NULL;}
 return &session.link.client;
}
void xsr_release(void) { k_mutex_unlock(&session_lock); }
int xsr_close(int64_t end) {
 if(lock_until(end))return -ETIMEDOUT;
 int rc=opened?xs_zephyr_close(&session,MAX(0,end-k_uptime_get())):0;
 if(!session.link.open)opened=false;
 k_mutex_unlock(&session_lock);return rc;
}
int xsr_unwatch(uint32_t watch,int64_t end) {
 struct xs_client *c=xsr_acquire(end);if(!c)return -ETIMEDOUT;
 int rc=xs_unwatch(c,watch,MAX(0,end-k_uptime_get()));xsr_release();return rc;
}
int xsr_watch_next_status(uint32_t watch,int ms,bool *unusable) {
 if(unusable)*unusable=false;
 int64_t end=k_uptime_get()+ms;
 struct xs_client *c=xsr_acquire(end);if(!c)return -ETIMEDOUT;
 struct xs_watch_event event;
 int rc=xs_watch_next(c,watch,&event,MAX(0,end-k_uptime_get()));
 if(unusable)*unusable=c->poisoned||c->closed;
 xsr_release();return rc;
}
int xsr_watch_next(uint32_t watch,int ms) {
 return xsr_watch_next_status(watch,ms,NULL);
}
int xsr_read_status(const char *path,char *value,size_t cap,int ms,bool *unusable) {
 if(unusable)*unusable=false;
 int64_t end=k_uptime_get()+ms;struct xs_client *c=xsr_acquire(end);if(!c)return -ETIMEDOUT;
 int left=MAX(0,end-k_uptime_get());
 int rc=left?xs_read_text(c,0,path,value,cap,left):-ETIMEDOUT;
 if(unusable)*unusable=c->poisoned||c->closed;
 xsr_release();return rc;
}
int xsr_read(const char *path,char *value,size_t cap,int ms) {
 return xsr_read_status(path,value,cap,ms,NULL);
}
int xsr_write(const char *path,const char *value,int ms) {
 int64_t end=k_uptime_get()+ms;struct xs_client *c=xsr_acquire(end);if(!c)return -ETIMEDOUT;
 int rc=xs_write(c,0,path,value,strlen(value),MAX(0,end-k_uptime_get()));xsr_release();return rc;
}

int xsr_transaction(int ms,xs_transaction_fn body,void *context) {
 if(ms<0||!body)return -EINVAL;
 int64_t end=k_uptime_get()+ms;
 struct xs_client *c=xsr_acquire(end);if(!c)return -ETIMEDOUT;
 unsigned int attempts=0;
 int rc=xs_transaction(c,XS_RETRY_LIMIT,MAX(0,end-k_uptime_get()),body,context,&attempts);
 xsr_release();return rc;
}
