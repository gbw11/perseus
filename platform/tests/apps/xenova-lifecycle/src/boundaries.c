/* Inject external failures/effects, never duplicate the lifecycle under test. */
#include <lifecycle_boundary.h>
#include <xenova/runtime.h>
#include <xenova/xenbus_vif.h>
#include <xenova/grant_pool.h>
#include <xenova/netfront_ring.h>
#include <assert.h>
#include <errno.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
const struct ethernet_api *xl_api;
const char *xl_case;
unsigned xl_hypercalls,xl_grants,xl_revokes,xl_opened,xl_closed,xl_features;
bool xl_retry,xl_race;
K_SEM_DEFINE(xl_acquired,0,1);
K_SEM_DEFINE(xl_release,0,1);
static struct xs_client client;
static struct xgp_entry table[512];
static struct xgp_pool pool={.table=table};
static unsigned front,watched;
static bool advertised;
static struct xnf_tx_ring *tx_ring;
static bool event_bound;
atomic_uint xl_polls;
atomic_uint xl_tx_done;
atomic_int xl_poll_error;
atomic_bool xl_wait_enabled;
#if defined(XL_PACKET_LOCK_TEST)
atomic_int xl_packet_lock_error;
static k_tid_t packet_thread;
int __real_z_impl_k_mutex_lock(struct k_mutex *,k_timeout_t);
int __wrap_z_impl_k_mutex_lock(struct k_mutex *m,k_timeout_t timeout) {
 int rc=k_current_get()==packet_thread?atomic_exchange(&xl_packet_lock_error,0):0;
 return rc?rc:__real_z_impl_k_mutex_lock(m,timeout);
}
#endif
K_SEM_DEFINE(xl_store_wait,0,1);
int __real_xnf_poll(struct xnf *);
int __wrap_xnf_poll(struct xnf *n) {
 atomic_fetch_add(&xl_polls,1);int rc=__real_xnf_poll(n);
 atomic_store(&xl_tx_done,n->stats.tx);
 if(rc){int empty=0;atomic_compare_exchange_strong(&xl_poll_error,&empty,rc);}return rc;
}
static bool is(const char *s) { return !strcmp(xl_case,s); }
k_tid_t __real_z_impl_k_thread_create(struct k_thread *,k_thread_stack_t *,size_t,k_thread_entry_t,void *,void *,void *,int,uint32_t,k_timeout_t);
k_tid_t __wrap_z_impl_k_thread_create(struct k_thread *t,k_thread_stack_t *stack,size_t size,k_thread_entry_t entry,void *a,void *b,void *c,int priority,uint32_t options,k_timeout_t delay) {
 static unsigned created;
 if(xl_case) {
  created++;
#if defined(XL_PACKET_LOCK_TEST)
  if(created==2)packet_thread=t;
#endif
  if((is("net-monitor-thread")&&created==1)||(is("net-packet-thread")&&created==2))return NULL;
 }
 return __real_z_impl_k_thread_create(t,stack,size,entry,a,b,c,priority,options,delay);
}
int xsr_open(void) { xl_opened++;return is("net-store")||is("shutdown-store")?-EIO:0; }
struct xs_client *xsr_acquire(int64_t end) {
 if(xl_wait_enabled&&front==4&&is("wait-lock")){k_sem_give(&xl_store_wait);return NULL;}
 return end>k_uptime_get()?&client:NULL;
}
void xsr_release(void) { }
int xs_read_text(struct xs_client *c,uint32_t tx,const char *p,char *v,size_t n,int ms) {
 ARG_UNUSED(c);ARG_UNUSED(tx);ARG_UNUSED(p);ARG_UNUSED(ms);snprintf(v,n,"42");return 0;
}
int xs_watch(struct xs_client *c,const char *p,uint32_t *id,int ms) {
 ARG_UNUSED(c);ARG_UNUSED(p);ARG_UNUSED(ms);*id=++watched;
 return is("shutdown-watch")||is("net-watch")?-ETIMEDOUT:0;
}
int xsr_unwatch(uint32_t id,int64_t end) { assert(id);return end<=k_uptime_get()?-ETIMEDOUT:0; }
static bool shutdown_watch_delivered;
static unsigned shutdown_watch_reads;
static int64_t shutdown_read_failed_at;
static bool shutdown_read_case(void) { return strstr(xl_case,"shutdown-external-read-")!=NULL; }
int xsr_watch_next(uint32_t id,int ms) {
 ARG_UNUSED(id);k_sleep(K_MSEC(ms));
 if(shutdown_read_case()){
  assert(!client.poisoned);
  if(!shutdown_watch_delivered){shutdown_watch_delivered=true;return 0;}
 }
 if(is("shutdown-external-watch-poison")){
  assert(!client.poisoned);
  client.poisoned=true;client.fatal_error=-ETIMEDOUT;
  return client.fatal_error;
 }
 if(xl_wait_enabled&&front==4){
  if(is("wait-watch-busy")){k_sem_give(&xl_store_wait);return -EBUSY;}
  if(is("wait-watch-poison")){client.poisoned=true;client.fatal_error=-ETIMEDOUT;k_sem_give(&xl_store_wait);}
  if(getenv("XL_WATCH_FLOOD"))return 0;
 }
 return client.poisoned?client.fatal_error:-ETIMEDOUT;
}
int xsr_watch_next_status(uint32_t id,int ms,bool *unusable) {
 int rc=xsr_watch_next(id,ms);if(unusable)*unusable=client.poisoned||client.closed;return rc;
}
int xsr_read(const char *p,char *v,size_t n,int ms) {
 if(ms<=0)return -ETIMEDOUT;
 if(strstr(p,"control/shutdown")) {
  if(shutdown_read_case()) {
   if(!shutdown_watch_delivered){snprintf(v,n,"%s","");return 0;}
   if(++shutdown_watch_reads==1) {
    shutdown_read_failed_at=k_uptime_get();
    if(is("shutdown-external-read-busy"))return -EBUSY;
    if(is("shutdown-external-read-again"))return -EAGAIN;
    if(is("shutdown-external-read-poison")){client.poisoned=true;client.fatal_error=-ETIMEDOUT;}
    return -ETIMEDOUT;
   }
   assert(shutdown_watch_reads==2&&!client.poisoned);
   assert(k_uptime_get()-shutdown_read_failed_at>=100);
  }
  bool external=strstr(xl_case,"external")&&!is("shutdown-external-watch-poison");
  snprintf(v,n,"%s",external?"reboot":"");return 0;
 }
 snprintf(v,n,"1");return 0;
}
int xsr_read_status(const char *p,char *v,size_t n,int ms,bool *unusable) {
 int rc=xsr_read(p,v,n,ms);if(unusable)*unusable=client.poisoned||client.closed;return rc;
}
int xsr_write(const char *p,const char *v,int ms) {
 if(ms<=0)return -ETIMEDOUT;
 if(client.poisoned)return client.fatal_error;
 if(strstr(p,"feature-")) {
  if(!strcmp(v,"1")) {
   xl_features++;
   if(is("shutdown-feature1")||(is("shutdown-feature2")&&xl_features==2))return -EIO;
  }
 } else if(strstr(p,"control/shutdown")) {
  if(is("shutdown-external-ack"))return -EIO;
 } else if(strstr(p,"/state"))front=(unsigned)(v[0]-'0');
 return 0;
}
int xs_write(struct xs_client *c,uint32_t tx,const char *p,const void *v,size_t n,int ms) {
 ARG_UNUSED(c);ARG_UNUSED(tx);ARG_UNUSED(n);return xsr_write(p,v,ms);
}
int xs_remaining(struct xs_client *c,int64_t end) { ARG_UNUSED(c);return MAX(0,end-k_uptime_get()); }
int xsr_transaction(int ms,xs_transaction_fn body,void *ctx) {
 int rc=body(&client,1,k_uptime_get()+ms,ctx);advertised=!rc;
 return is("net-publish-lost")?-ETIMEDOUT:rc;
}
int xsr_close(int64_t end) {
 assert(end>=k_uptime_get());xl_closed++;return is("shutdown-external-store")?-EIO:0;
}
int xvif_discover(struct xs_client *c,struct xvif *v,int ms) {
 ARG_UNUSED(c);ARG_UNUSED(ms);*v=(struct xvif){.domid=42,.backend_domid=0,.backend_state=XenbusStateInitWait};
 strcpy(v->frontend,"/local/domain/42/device/vif/0");strcpy(v->backend,"/local/domain/0/backend/vif/42/0");
 if(is("net-init-wait"))v->backend_state=XenbusStateInitialising;
 return is("net-discover")?-EIO:0;
}
int xvif_watch_backend(struct xs_client *c,const struct xvif *v,uint32_t *id,int ms) {
 ARG_UNUSED(v);return xs_watch(c,"/backend",id,ms);
}
int xvif_refresh(struct xs_client *c,struct xvif *v,int ms) {
 if(ms<=0)return -ETIMEDOUT;
 if(c->poisoned)return c->fatal_error;
 if(xl_wait_enabled&&(front==4||getenv("XL_CLOSE_UNAVAILABLE"))) {
  k_sem_give(&xl_store_wait);
  if(is("wait-delay"))k_sleep(K_MSEC(35));
  if(is("wait-timeout"))return -ETIMEDOUT;
  if(is("wait-busy"))return -EBUSY;
  if(is("wait-again"))return -EAGAIN;
  if(is("wait-poison")){c->poisoned=true;c->fatal_error=-ETIMEDOUT;return c->fatal_error;}
  if(is("wait-stop-race")){k_sem_give(&xl_acquired);assert(!k_sem_take(&xl_release,K_SECONDS(1)));}
  if(is("wait-identity"))return -EXDEV;
  if(is("wait-protocol"))return -EPROTO;
  if(is("wait-closing")){v->backend_state=XenbusStateClosing;return 0;}
  if(is("wait-closed")){v->backend_state=XenbusStateClosed;return 0;}
 }
 if((is("net-backend")&&!xl_retry)||is("net-init-wait"))return -ETIMEDOUT;
 v->backend_state=front==6?XenbusStateClosed:front==5?XenbusStateClosing:advertised?XenbusStateConnected:XenbusStateInitWait;
 if(is("net-close-timeout")&&!xl_retry&&front>=5)v->backend_state=XenbusStateConnected;
 return 0;
}
int xgrant_init(void) { return 0; }
int xgrant_share(uint16_t backend,void *page,bool ro,uint32_t *ref) {
 ARG_UNUSED(page);
 if(is("net-grant")&&xl_grants==3)return -ENOMEM;
 int rc=xgp_share(&pool,backend,1,ro,ref);if(!rc)xl_grants++;
 if(!rc&&xl_grants==1)tx_ring=page;
 if(xl_race&&xl_grants==1){k_sem_give(&xl_acquired);k_sem_take(&xl_release,K_FOREVER);}
 return rc;
}
int xgrant_revoke_until(uint32_t ref,int64_t end) {
 if(k_uptime_get()>=end)return -ETIMEDOUT;
 assert(!advertised||front==6); /* No reuse after a lost publish response. */
 if(is("net-busy")&&!xl_retry&&ref==XGP_FIRST+1)return -EBUSY;
 int rc=xgp_revoke(&pool,ref);assert(rc!=-ENOENT);if(!rc)xl_revokes++;return rc;
}
int xgrant_revoke(uint32_t ref) { return xgrant_revoke_until(ref,k_uptime_get()+20); }
int alloc_unbound_event_channel(unsigned dom) { ARG_UNUSED(dom);return is("net-event")?-ENOMEM:11; }
void mask_event_channel(unsigned p) { assert(p==11); }
int bind_event_channel(unsigned p,void (*fn)(void *),void *ctx) {
 ARG_UNUSED(fn);ARG_UNUSED(ctx);assert(p==11);if(is("net-bind"))return -EIO;event_bound=true;return 0;
}
void unbind_event_channel(unsigned p) { assert(p==11&&event_bound);event_bound=false; }
int evtchn_close(unsigned p) { assert(p==11&&!event_bound);return 0; }
int notify_evtchn(unsigned p) { assert(p==11);return 0; }
int HYPERVISOR_event_channel_op(unsigned op,void *p) { ARG_UNUSED(op);ARG_UNUSED(p);return is("net-unmask")?-EIO:0; }
int HYPERVISOR_sched_op(unsigned op,void *p) { assert(op==SCHEDOP_shutdown);assert(((struct sched_shutdown *)p)->reason==1);xl_hypercalls++;return 0; }
void net_if_flag_set(struct net_if *i,int f) { ARG_UNUSED(i);ARG_UNUSED(f); }
void ethernet_init(struct net_if *i) { ARG_UNUSED(i); }
void net_if_carrier_off(struct net_if *i) { ARG_UNUSED(i); }
void net_eth_carrier_off(struct net_if *i) { ARG_UNUSED(i); }
void net_eth_carrier_on(struct net_if *i) { ARG_UNUSED(i); }
int net_if_up(struct net_if *i) { ARG_UNUSED(i);return is("net-interface")?-EIO:0; }
int net_if_down(struct net_if *i) { ARG_UNUSED(i);return 0; }
void net_if_set_link_addr(struct net_if *i,const uint8_t *a,size_t n,int t) { ARG_UNUSED(i);ARG_UNUSED(a);ARG_UNUSED(n);ARG_UNUSED(t); }
void net_dhcpv4_start(struct net_if *i) { ARG_UNUSED(i); }
void net_dhcpv4_stop(struct net_if *i) { ARG_UNUSED(i); }
struct net_pkt *net_pkt_rx_alloc_with_buffer(struct net_if *i,size_t n,int a,int p,k_timeout_t t) { ARG_UNUSED(i);ARG_UNUSED(n);ARG_UNUSED(a);ARG_UNUSED(p);ARG_UNUSED(t);return NULL; }
int net_pkt_write(struct net_pkt *p,const void *d,size_t n) { ARG_UNUSED(p);ARG_UNUSED(d);ARG_UNUSED(n);return 0; }
int net_recv_data(struct net_if *i,struct net_pkt *p) { ARG_UNUSED(i);ARG_UNUSED(p);return 0; }
void net_pkt_unref(struct net_pkt *p) { ARG_UNUSED(p); }
size_t net_pkt_get_len(struct net_pkt *p) { return p->length; }
int net_pkt_read(struct net_pkt *p,void *out,size_t n) {
 if(is("net-send-race")){k_sem_give(&xl_acquired);k_sem_take(&xl_release,K_FOREVER);}
 else if(!strncmp(xl_case,"packet-",7)){
  k_sem_give(&xl_acquired);assert(!k_sem_take(&xl_release,K_SECONDS(3)));
 }
 memcpy(out,p->bytes,n);return 0;
}
static void complete_tx(bool corrupt) {
 uint32_t end=__atomic_load_n(&tx_ring->h.req_prod,__ATOMIC_ACQUIRE);
 for(uint32_t i=tx_ring->h.rsp_prod;i<end;i++) {
  uint16_t id=tx_ring->ring[i&(XNF_RING_SIZE-1)].req.id;
  tx_ring->ring[i&(XNF_RING_SIZE-1)].rsp=(struct xnf_tx_response){.id=corrupt?UINT16_MAX:id,.status=0};
 }
 __atomic_store_n(&tx_ring->h.rsp_prod,end,__ATOMIC_RELEASE);
}
void xl_complete_tx(void) { complete_tx(false); }
void xl_corrupt_tx(void) { complete_tx(true); }
