/* SPDX-License-Identifier: Apache-2.0 */
/* Ring validation and ownership are shared by the real driver and native tests. */
#include <xenova/netfront_ring.h>
#include <errno.h>
#include <string.h>
_Static_assert(sizeof(struct xnf_tx_request)==12, "Xen TX ABI");
_Static_assert(sizeof(struct xnf_rx_response)==8, "Xen RX ABI");
_Static_assert(offsetof(struct xnf_tx_ring,ring)==64, "Xen ring header");
_Static_assert(sizeof(struct xnf_tx_ring)<=XNF_PAGE, "TX ring page");
_Static_assert(sizeof(struct xnf_rx_ring)<=XNF_PAGE, "RX ring page");

static uint32_t load(volatile uint32_t *p) { return __atomic_load_n(p,__ATOMIC_ACQUIRE); }
static void publish(struct xnf *n, struct xnf_header *h, uint32_t prod) {
 uint32_t old=load(&h->req_prod);
 __atomic_store_n(&h->req_prod,prod,__ATOMIC_RELEASE);
 __atomic_thread_fence(__ATOMIC_SEQ_CST);
 if ((uint32_t)(prod-load(&h->req_event))<(uint32_t)(prod-old)) n->notify(n->context);
}
static int bad(struct xnf *n) { n->failed=true; n->stats.protocol_errors++; return -EPROTO; }
static void bytes(struct xnf *n,uint64_t *count,size_t size){
 if(UINT64_MAX-*count<size){*count=UINT64_MAX;n->stats.bytes_saturated=true;}else *count+=size;
}
static void post(struct xnf *n, unsigned id) {
 n->rx_id[id]+=XNF_SLOTS;
 n->rx->ring[n->rx_prod & (XNF_RING_SIZE-1)].req=(struct xnf_rx_request){n->rx_id[id],0,n->rx_grants[id]};
 n->rx_posted[id]=true; n->rx_prod++;
}
static int reap_tx(struct xnf *n,uint32_t producer,unsigned *budget) {
 if((uint32_t)(producer-n->tx_cons)>(uint32_t)(n->tx_prod-n->tx_cons))return bad(n);
 while(n->tx_cons!=producer&&*budget) {
  struct xnf_tx_response v=n->tx->ring[n->tx_cons & (XNF_RING_SIZE-1)].rsp;
  unsigned id=v.id&(XNF_SLOTS-1);
  if(v.id!=n->tx_id[id]||!n->tx_busy[id]||v.status>0||v.status< -2)return bad(n);
  n->tx_busy[id]=false;n->tx_cons++;(*budget)--;
  if(v.status)n->stats.tx_drop++;else n->stats.tx++;
 }
 return 0;
}
void xnf_init(struct xnf *n) {
 n->stats.tx_bytes=n->stats.rx_bytes=0;n->stats.bytes_saturated=n->stats.counter_epoch==UINT64_MAX;
 if(!n->stats.bytes_saturated)n->stats.counter_epoch++;
 memset(n->tx,0,XNF_PAGE);memset(n->rx,0,XNF_PAGE);
 n->tx_prod=n->tx_cons=n->rx_prod=n->rx_cons=0;n->failed=false;
 for(unsigned i=0;i<XNF_SLOTS;i++){n->tx_id[i]=i;n->rx_id[i]=i;}
 memset(n->tx_busy,0,sizeof(n->tx_busy));memset(n->rx_posted,0,sizeof(n->rx_posted));
 n->tx->h.req_event=n->tx->h.rsp_event=n->rx->h.req_event=n->rx->h.rsp_event=1;
 for (unsigned i=0;i<XNF_SLOTS;i++)post(n,i);
 publish(n,&n->rx->h,n->rx_prod);
}
int xnf_send(struct xnf *n,const void *data,size_t size) {
 if(n->failed)return -ENETDOWN;
 if(!data||size<14||size>XNF_FRAME_MAX)return -EMSGSIZE;
 unsigned id=0;while(id<XNF_SLOTS&&n->tx_busy[id])id++;
 if(id==XNF_SLOTS) {
  /* The caller serializes ring access. Reclaim only TX: a full poll would
   * deliver RX recursively from the TCP send path on its small stack. */
  unsigned budget=XNF_SLOTS;
  for(;;) {
   int rc=reap_tx(n,load(&n->tx->h.rsp_prod),&budget);if(rc)return rc;
   __atomic_store_n(&n->tx->h.rsp_event,n->tx_cons+1,__ATOMIC_RELEASE);
   __atomic_thread_fence(__ATOMIC_SEQ_CST);
   if(!budget||load(&n->tx->h.rsp_prod)==n->tx_cons)break;
  }
  id=0;while(id<XNF_SLOTS&&n->tx_busy[id])id++;
  if(id==XNF_SLOTS){n->stats.pool_busy++;return -ENOBUFS;}
 }
 if((uint32_t)(n->tx_prod-n->tx_cons)>=XNF_RING_SIZE)return bad(n);
 memcpy(n->tx_pages[id],data,size);
 n->tx_busy[id]=true;n->tx_id[id]+=XNF_SLOTS;
 n->tx->ring[n->tx_prod & (XNF_RING_SIZE-1)].req=(struct xnf_tx_request){n->tx_grants[id],0,0,n->tx_id[id],size};
 n->tx_prod++;publish(n,&n->tx->h,n->tx_prod);bytes(n,&n->stats.tx_bytes,size);return 0;
}
int xnf_poll(struct xnf *n) {
 if(n->failed)return -EPROTO;
 unsigned budget=64;
 for(;;) {
  uint32_t t=load(&n->tx->h.rsp_prod),r=load(&n->rx->h.rsp_prod);
  if((uint32_t)(r-n->rx_cons)>(uint32_t)(n->rx_prod-n->rx_cons))return bad(n);
  int rc=reap_tx(n,t,&budget);if(rc)return rc;
  while(n->rx_cons!=r&&budget) {
   struct xnf_rx_response v=n->rx->ring[n->rx_cons & (XNF_RING_SIZE-1)].rsp;
   unsigned id=v.id&(XNF_SLOTS-1);
   if(v.id!=n->rx_id[id]||!n->rx_posted[id])return bad(n);
   /* No partially accepted fragments/extra-info/checksum-blank data. */
   if(v.flags & ~1U)return bad(n);
   n->rx_posted[id]=false;
   if(v.status<0){n->stats.rx_drop++;}
   else if(v.status<14||v.status>XNF_FRAME_MAX||v.offset>XNF_PAGE-(unsigned)v.status)return bad(n);
   else if(n->deliver(n->context,n->rx_pages[id]+v.offset,(size_t)v.status))n->stats.rx_drop++;
   else {n->stats.rx++;bytes(n,&n->stats.rx_bytes,(size_t)v.status);}
   n->rx_cons++;budget--;post(n,id);
  }
  publish(n,&n->rx->h,n->rx_prod);
  __atomic_store_n(&n->tx->h.rsp_event,n->tx_cons+1,__ATOMIC_RELEASE);
  __atomic_store_n(&n->rx->h.rsp_event,n->rx_cons+1,__ATOMIC_RELEASE);
  __atomic_thread_fence(__ATOMIC_SEQ_CST);
  if(!budget|| (load(&n->tx->h.rsp_prod)==n->tx_cons&&load(&n->rx->h.rsp_prod)==n->rx_cons))return 0;
 }
}
bool xnf_drained(const struct xnf *n) {
 for(unsigned i=0;i<XNF_SLOTS;i++)if(n->tx_busy[i])return false;
 return true;
}
bool xnf_pending(const struct xnf *n) {
 return __atomic_load_n(&n->tx->h.rsp_prod,__ATOMIC_ACQUIRE)!=n->tx_cons ||
        __atomic_load_n(&n->rx->h.rsp_prod,__ATOMIC_ACQUIRE)!=n->rx_cons;
}
