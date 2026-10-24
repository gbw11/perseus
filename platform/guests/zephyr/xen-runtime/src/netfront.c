/* SPDX-License-Identifier: Apache-2.0 */
/* One VIF / one queue pair, RX-copy. ISR only wakes the bounded worker.
 * Pages are statically pinned for the domain lifetime; failed revocation never
 * permits reuse. No reconnect, grant transfer, offload, or multi-page frame. */
#include <xenova/netfront.h>
#include <xenova/runtime.h>
#include <xenova/xenbus_vif.h>
#include <zephyr/device.h>
#include <zephyr/net/ethernet.h>
#include <zephyr/net/dhcpv4.h>
#include <zephyr/xen/events.h>
#include <zephyr/arch/arm64/hypercall.h>
#include <zephyr/sys/printk.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdatomic.h>
BUILD_ASSERT(CONFIG_XENOVA_NETFRONT_THREAD_PRIORITY<CONFIG_NUM_PREEMPT_PRIORITIES,"invalid netfront priority");
static uint8_t tx_ring_page[4096] __aligned(4096),rx_ring_page[4096] __aligned(4096);
static uint8_t tx_pages[XNF_SLOTS][4096] __aligned(4096),rx_pages[XNF_SLOTS][4096] __aligned(4096);
static struct xnf n;
static struct xvif vif;
static struct net_if *iface;
static int port=-1;
static uint32_t tx_ring_ref,rx_ring_ref,backend_watch;
static uint32_t grants[2+2*XNF_SLOTS];
static unsigned grant_count;
static atomic_bool started,connected,closing,worker_done;
static bool worker_started,published;
static const char *startup_stage="not-started";
static struct k_thread worker;
K_THREAD_STACK_DEFINE(worker_stack,6144);
K_MUTEX_DEFINE(packet_lock);
K_MUTEX_DEFINE(lifecycle_lock);
K_SEM_DEFINE(net_wake,0,1);
static void event(void *arg) { ARG_UNUSED(arg);k_sem_give(&net_wake); }
static void notify(void *arg) { ARG_UNUSED(arg);if(port>=0)(void)notify_evtchn(port); }
static int deliver(void *arg,const uint8_t *data,size_t len) {
 ARG_UNUSED(arg);
 struct net_pkt *pkt=net_pkt_rx_alloc_with_buffer(iface,len,NET_AF_UNSPEC,0,K_NO_WAIT);
 if(!pkt)return -ENOMEM;
 int rc=net_pkt_write(pkt,data,len);
 if(!rc)rc=net_recv_data(iface,pkt);
 if(rc)net_pkt_unref(pkt);
 return rc;
}
static int write_child(struct xs_client *c,uint32_t tx,const char *key,const char *value,int64_t end) {
 char path[XS_PATH_LIMIT];
 if(snprintf(path,sizeof(path),"%s/%s",vif.frontend,key)>=(int)sizeof(path))return -ENAMETOOLONG;
 return xs_write(c,tx,path,value,strlen(value),xs_remaining(c,end));
}
static int publish(struct xs_client *c,uint32_t tx,int64_t end,void *arg) {
 ARG_UNUSED(arg);char text[24];int rc;
 const char *keys[]={"tx-ring-ref","rx-ring-ref","event-channel"};
 uint32_t values[]={tx_ring_ref,rx_ring_ref,(uint32_t)port};
 for(unsigned i=0;i<3;i++){
  snprintf(text,sizeof(text),"%u",values[i]);
  rc=write_child(c,tx,keys[i],text,end);if(rc)return rc;
 }
 const char *features[][2]={{"request-rx-copy","1"},{"feature-rx-notify","1"},
  {"feature-no-csum-offload","1"},{"feature-ipv6-csum-offload","0"},
  {"feature-gso-tcpv4","0"},{"feature-gso-tcpv6","0"},{"feature-sg","0"},
  {"trusted","0"},{"state","4"}};
 for(unsigned i=0;i<ARRAY_SIZE(features);i++){
  rc=write_child(c,tx,features[i][0],features[i][1],end);if(rc)return rc;
 }
 return 0;
}
static int frontend_state(const char *value,int ms) {
 char path[XS_PATH_LIMIT];snprintf(path,sizeof(path),"%s/state",vif.frontend);
 return xsr_write(path,value,ms);
}
static int backend_state(int ms) {
 int64_t end=k_uptime_get()+ms;
 struct xs_client *c=xsr_acquire(end);if(!c)return -ETIMEDOUT;
 int rc=xvif_refresh(c,&vif,MAX(0,end-k_uptime_get()));xsr_release();
 return rc?rc:(int)vif.backend_state;
}
static int share(void *page,bool ro,uint32_t *ref) {
 int rc=xgrant_share(vif.backend_domid,page,ro,ref);
 if(!rc)grants[grant_count++]=*ref;
 return rc;
}
static void loop(void *a,void *b,void *c) {
 ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);
 int rc=0;int64_t next_state=0;
 while(!closing) {
  if(k_mutex_lock(&packet_lock,K_MSEC(20))){rc=-ETIMEDOUT;break;}
  rc=xnf_poll(&n);k_mutex_unlock(&packet_lock);if(rc)break;
  if(k_uptime_get()>=next_state) {
   int state=backend_state(50);
   if(state<0||state>=XenbusStateClosing){rc=state<0?state:-ENETDOWN;break;}
   next_state=k_uptime_get()+250;
  }
  (void)k_sem_take(&net_wake,K_MSEC(10));
 }
 if(rc){connected=false;net_eth_carrier_off(iface);printk("XENOVA netfront link down rc=%d\n",rc);}
 worker_done=true;
}
static void iface_init(struct net_if *i) {
 iface=i;
 /* MAC comes from this execution's XenStore. Do not let net_if_post_init()
  * bring the interface up before identity and backend are bound. */
 net_if_flag_set(i,NET_IF_NO_AUTO_START);
 ethernet_init(i);
 net_if_carrier_off(i);
}
static int init(const struct device *dev) { ARG_UNUSED(dev);return 0; }
static enum ethernet_hw_caps caps(const struct device *dev) { ARG_UNUSED(dev);return 0; }
/* Ethernet send also runs on Zephyr's 1 KiB TCP workqueue. An MTU-sized
 * automatic buffer overflows that caller's stack; reuse packet_lock instead. */
static uint8_t tx_frame[XNF_FRAME_MAX];
static int send(const struct device *dev,struct net_pkt *pkt) {
 ARG_UNUSED(dev);
 if(!connected||closing)return -ENETDOWN;
 size_t len=net_pkt_get_len(pkt);
 if(len>sizeof(tx_frame)||len<14)return -EMSGSIZE;
 if(k_mutex_lock(&packet_lock,K_MSEC(20)))return -ETIMEDOUT;
 int rc=net_pkt_read(pkt,tx_frame,len);
 if(rc){k_mutex_unlock(&packet_lock);return rc;}
 rc=xnf_send(&n,tx_frame,len);k_mutex_unlock(&packet_lock);k_sem_give(&net_wake);return rc;
}
static const struct ethernet_api api={.iface_api.init=iface_init,.get_capabilities=caps,.send=send};
ETH_NET_DEVICE_INIT(xenova_netfront,"xenova-netfront",init,NULL,NULL,NULL,80,&api,1500);

static int start_inner(void) {
 if(started||!iface)return -EALREADY;
 started=true;startup_stage="store-open";int rc=xsr_open();if(rc)return rc;
 int64_t end=k_uptime_get()+10000;
 struct xs_client *c=xsr_acquire(end);if(!c)return -ETIMEDOUT;
 startup_stage="vif-discover";rc=xvif_discover(c,&vif,500);
 if(!rc){startup_stage="backend-watch";rc=xvif_watch_backend(c,&vif,&backend_watch,500);}
 xsr_release();if(rc)return rc;
 /* Current implementation supports dom0 netback only; no ownership guess. */
 if(vif.backend_domid!=0)return -ENOTSUP;
 startup_stage="backend-init-wait";
 while(vif.backend_state!=XenbusStateInitWait) {
  int state=backend_state(100);
  if(state<0||state>=XenbusStateClosing)return state<0?state:-ENETDOWN;
  if(k_uptime_get()>=end)return -ETIMEDOUT;
  (void)xsr_watch_next(backend_watch,10);
 }

 char path[XS_PATH_LIMIT],feature[16];
 snprintf(path,sizeof(path),"%s/feature-rx-copy",vif.backend);
 startup_stage="rx-copy-feature";rc=xsr_read(path,feature,sizeof(feature),500);
 if(rc||strcmp(feature,"1"))return rc?rc:-ENOTSUP;
 startup_stage="grant-init";rc=xgrant_init();if(rc)return rc;
 memset(&n,0,sizeof(n));n.tx=(void *)tx_ring_page;n.rx=(void *)rx_ring_page;
 n.tx_pages=tx_pages;n.rx_pages=rx_pages;n.notify=notify;n.deliver=deliver;
 startup_stage="grant-share";rc=share(tx_ring_page,false,&tx_ring_ref);if(rc)return rc;
 rc=share(rx_ring_page,false,&rx_ring_ref);if(rc)return rc;
 for(unsigned i=0;i<XNF_SLOTS;i++){
  rc=share(tx_pages[i],true,&n.tx_grants[i]);if(rc)return rc;
  rc=share(rx_pages[i],false,&n.rx_grants[i]);if(rc)return rc;
 }
 startup_stage="event-allocate";port=alloc_unbound_event_channel(vif.backend_domid);
 if(port<=0||port>=EVTCHN_2L_NR_CHANNELS)return -ERANGE;
 mask_event_channel(port);
 startup_stage="event-bind";rc=bind_event_channel(port,event,NULL);if(rc)return rc;
 xnf_init(&n);
 struct evtchn_unmask unmask={.port=port};
 startup_stage="event-unmask";rc=HYPERVISOR_event_channel_op(EVTCHNOP_unmask,&unmask);if(rc)return rc;
 startup_stage="frontend-publish";
 rc=xsr_transaction(MIN(1000,MAX(0,end-k_uptime_get())),publish,NULL);if(rc)return rc;
 published=true;startup_stage="backend-connected-wait";
 for(;;) {
  int state=backend_state(100);
  if(state==XenbusStateConnected)break;
  if(state<0||state>=XenbusStateClosing)return state<0?state:-ENETDOWN;
  if(k_uptime_get()>=end)return -ETIMEDOUT;
  (void)xsr_watch_next(backend_watch,10);
 }
 net_if_set_link_addr(iface,vif.mac,6,NET_LINK_ETHERNET);
 connected=true;net_eth_carrier_on(iface);
 worker_started=true;
 k_thread_create(&worker,worker_stack,K_THREAD_STACK_SIZEOF(worker_stack),loop,NULL,NULL,NULL,CONFIG_XENOVA_NETFRONT_THREAD_PRIORITY,0,K_NO_WAIT);
 startup_stage="interface-up";rc=net_if_up(iface);if(rc&&rc!=-EALREADY)return rc;
 net_dhcpv4_start(iface);startup_stage="dhcp-started";
 printk("XENOVA netfront connected domid=%u backend=%u port=%d mtu=1500 rx-copy slots=%u\n",
        vif.domid,vif.backend_domid,port,XNF_SLOTS);
 return 0;
}
static bool drained(void) {
 if(k_mutex_lock(&packet_lock,K_MSEC(20)))return false;
 bool result=xnf_drained(&n);k_mutex_unlock(&packet_lock);return result;
}
static int close_inner(int64_t end) {
 if(!started)return 0;
 /* Keep worker and transport alive while application final-state hook runs.
  * Hook has returned before this call; now prevent new TX and drain existing TX. */
 connected=false;
 while(!drained()&&k_uptime_get()<end) {
  k_sem_give(&net_wake);k_sleep(K_MSEC(10));
 }
 if(!drained())return -ETIMEDOUT;
 closing=true;k_sem_give(&net_wake);
 while(worker_started&&!worker_done&&k_uptime_get()<end)k_sleep(K_MSEC(5));
 if(worker_started&&!worker_done)return -ETIMEDOUT;
 net_eth_carrier_off(iface);net_dhcpv4_stop(iface);
 int rc=0;
 if(published) {
 rc=frontend_state("5",MIN(100,MAX(0,end-k_uptime_get())));if(rc)return rc;
 /* Linux netback acknowledges Closing before disconnecting on frontend Closed. */
 while(k_uptime_get()<end) {
  int state=backend_state(MIN(100,MAX(0,end-k_uptime_get())));
  if(state==XenbusStateClosing||state==XenbusStateClosed)break;
  if(state<0)return state;
  k_sleep(K_MSEC(10));
 }
 if(k_uptime_get()>=end)return -ETIMEDOUT;
 rc=frontend_state("6",MIN(100,MAX(0,end-k_uptime_get())));if(rc)return rc;
 while(k_uptime_get()<end) {
  int state=backend_state(MIN(100,MAX(0,end-k_uptime_get())));
  if(state==XenbusStateClosed)break;
  if(state<0)return state;
  k_sleep(K_MSEC(10));
 }
 if(k_uptime_get()>=end)return -ETIMEDOUT;
 }
 if(port>=0){mask_event_channel(port);unbind_event_channel(port);rc=evtchn_close(port);if(rc)return rc;port=-1;}
 for(unsigned i=0;i<grant_count;i++){
  do {rc=xgrant_revoke(grants[i]);if(rc==-EBUSY)k_sleep(K_MSEC(5));}
  while(rc==-EBUSY&&k_uptime_get()<end);
  if(rc)return rc; /* Quarantine all static pages if revocation is unconfirmed. */
 }
 printk("XENOVA netfront drained and owned grants revoked\n");return 0;
}
int xnet_start(void) {
 if(k_mutex_lock(&lifecycle_lock,K_MSEC(1000)))return -ETIMEDOUT;
 int rc=start_inner();if(rc)printk("XENOVA netfront startup stage=%s rc=%d\n",startup_stage,rc);k_mutex_unlock(&lifecycle_lock);return rc;
}
int xnet_close(int64_t end) {
 int64_t left=end-k_uptime_get();if(left<=0)return -ETIMEDOUT;
 if(k_mutex_lock(&lifecycle_lock,K_MSEC(left)))return -ETIMEDOUT;
 int rc=close_inner(end);k_mutex_unlock(&lifecycle_lock);return rc;
}
int xnet_stats(struct xnf_stats *out) {
 if(!out)return -EINVAL;
 if(k_mutex_lock(&packet_lock,K_MSEC(20)))return -ETIMEDOUT;
 *out=n.stats;k_mutex_unlock(&packet_lock);return 0;
}
