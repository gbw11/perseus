/* SPDX-License-Identifier: Apache-2.0 */
/* Exercise the production ring code, not a replacement packet implementation. */
#include <xenova/netfront_ring.h>
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
static unsigned char txring[4096] __attribute__((aligned(4096)));
static unsigned char rxring[4096] __attribute__((aligned(4096)));
static unsigned char txdata[XNF_SLOTS][4096],rxdata[XNF_SLOTS][4096];
static struct xnf n;static unsigned notices,delivered;static int drop;
static bool stream;
static uint32_t backend_rx;
static void backend_receive(void) {
 uint32_t end=n.rx->h.req_prod;
 while(backend_rx!=end) {
  unsigned slot=backend_rx&(XNF_RING_SIZE-1);
  uint16_t id=n.rx->ring[slot].req.id;
  memset(n.rx_pages[id&(XNF_SLOTS-1)],0x5a,64);
  n.rx->ring[slot].rsp=(struct xnf_rx_response){.id=id,.status=64};backend_rx++;
 }
 n.rx->h.rsp_prod=backend_rx;n.rx->h.req_event=end+1;
}
static void notify(void *p){(void)p;notices++;if(stream)backend_receive();}
static int deliver(void *p,const uint8_t *b,size_t len){(void)p;assert(len==64);assert(b[0]==0x5a);delivered++;return drop;}
static void init(void){
 memset(&n,0,sizeof(n));notices=delivered=drop=0;stream=false;backend_rx=0;
 n.tx=(void *)txring;n.rx=(void *)rxring;n.tx_pages=txdata;n.rx_pages=rxdata;
 n.notify=notify;n.deliver=deliver;
 for(unsigned i=0;i<XNF_SLOTS;i++){n.tx_grants[i]=10+i;n.rx_grants[i]=20+i;}
 xnf_init(&n);
}
static void rx(unsigned sequence,uint16_t id,uint16_t offset,uint16_t flags,int16_t status){
 n.rx->ring[sequence&255].rsp=(struct xnf_rx_response){id,offset,flags,status};
 n.rx->h.rsp_prod=sequence+1;
}
static void tx(unsigned sequence,uint16_t id,int16_t status){
 n.tx->ring[sequence&255].rsp=(struct xnf_tx_response){id,status};
 n.tx->h.rsp_prod=sequence+1;
}
int main(void){
 unsigned cases=0;uint8_t packet[64];memset(packet,0x5a,sizeof(packet));
 init();assert(n.rx_prod==8&&notices==1);cases++;
 assert(xnf_send(&n,NULL,64)==-EMSGSIZE);assert(xnf_send(&n,packet,13)==-EMSGSIZE);cases++;
 assert(xnf_send(&n,packet,1515)==-EMSGSIZE);cases++;
 assert(!xnf_send(&n,packet,64));uint16_t first=n.tx->ring[0].req.id;
 assert(n.tx->ring[0].req.flags==0&&n.tx->ring[0].req.gref==10);cases++;
 packet[0]=0;assert(n.tx_pages[0][0]==0x5a);packet[0]=0x5a;cases++;
 for(unsigned i=1;i<XNF_SLOTS;i++)assert(!xnf_send(&n,packet,64));
 assert(xnf_send(&n,packet,64)==-ENOBUFS&&n.stats.pool_busy==1);cases++;
 tx(0,first,0);assert(!xnf_poll(&n)&&n.stats.tx==1);assert(!xnf_send(&n,packet,64));cases++;
 tx(1,first,0);assert(xnf_poll(&n)==-EPROTO&&n.failed);cases++;
 init();assert(!xnf_send(&n,packet,64));tx(0,65535,0);assert(xnf_poll(&n)==-EPROTO);cases++;
 init();assert(!xnf_send(&n,packet,64));tx(0,n.tx->ring[0].req.id,-2);
 assert(!xnf_poll(&n)&&n.stats.tx_drop==1&&xnf_drained(&n));cases++;
 for(int st=-3;st<=1;st+=4){
  init();assert(!xnf_send(&n,packet,64));tx(0,n.tx->ring[0].req.id,st);assert(xnf_poll(&n)==-EPROTO);cases++;
 }
 init();n.tx->h.rsp_prod=1;assert(xnf_poll(&n)==-EPROTO);cases++;
 init();n.rx->h.rsp_prod=9;assert(xnf_poll(&n)==-EPROTO);cases++;
 init();memcpy(n.rx_pages[0]+4096-64,packet,64);
 rx(0,n.rx_id[0],4096-64,1,64);
 assert(!xnf_poll(&n)&&delivered==1&&n.stats.rx==1&&n.rx_prod==9);cases++;
 init();memcpy(n.rx_pages[0],packet,64);uint16_t id=n.rx_id[0];
 rx(0,id,0,0,64);assert(!xnf_poll(&n));
 rx(1,id,0,0,64);assert(xnf_poll(&n)==-EPROTO);cases++;
 for(unsigned flag=2;flag<=32;flag<<=1){
  init();rx(0,n.rx_id[0],0,flag,64);assert(xnf_poll(&n)==-EPROTO&&delivered==0);cases++;
 }
 init();rx(0,n.rx_id[0],4090,0,64);assert(xnf_poll(&n)==-EPROTO);cases++;
 init();rx(0,n.rx_id[0],0,0,1515);assert(xnf_poll(&n)==-EPROTO);cases++;
 init();rx(0,n.rx_id[0],0,0,0);assert(xnf_poll(&n)==-EPROTO);cases++;
 init();rx(0,n.rx_id[0],0,0,-1);assert(!xnf_poll(&n)&&n.stats.rx_drop==1);cases++;
 init();memcpy(n.rx_pages[0],packet,64);drop=-ENOMEM;
 rx(0,n.rx_id[0],0,0,64);assert(!xnf_poll(&n)&&n.stats.rx_drop==1&&n.rx_posted[0]);cases++;
 drop=0;rx(1,n.rx_id[0],0,0,64);
 assert(!xnf_poll(&n)&&n.stats.rx==1&&n.stats.rx_drop==1&&n.rx_posted[0]&&!n.failed);cases++;
 init();for(unsigned i=0;i<70000;i++){
  assert(!xnf_send(&n,packet,64));
  tx(i,n.tx->ring[i&255].req.id,0);
  memcpy(n.rx_pages[i&7],packet,64);
  rx(i,n.rx_id[i&7],0,0,64);
  assert(!xnf_poll(&n));assert(xnf_drained(&n));
 }
 assert(n.stats.tx==70000&&n.stats.rx==70000);
 assert(n.stats.tx_bytes==70000ULL*64&&n.stats.rx_bytes==70000ULL*64);cases++;
 uint64_t epoch=n.stats.counter_epoch;xnf_init(&n);
 assert(n.stats.counter_epoch==epoch+1&&!n.stats.tx_bytes&&!n.stats.rx_bytes);cases++;
 init();n.stats.tx_bytes=UINT64_MAX-10;assert(!xnf_send(&n,packet,64));
 assert(n.stats.tx_bytes==UINT64_MAX&&n.stats.bytes_saturated);cases++;
 init();n.tx_prod=n.tx_cons=UINT32_MAX-3;n.rx_prod=4;n.rx_cons=UINT32_MAX-3;
 n.tx->h.req_prod=n.tx_prod;n.rx->h.rsp_prod=n.rx_cons;
 for(unsigned i=0;i<12;i++){
  assert(!xnf_send(&n,packet,64));
  uint32_t sequence=n.tx_cons;tx(sequence,n.tx->ring[sequence&255].req.id,0);
  assert(!xnf_poll(&n));
 }
 cases++;
 /* The backend continuously replenishes RX, yet the production poll returns
  * at 64 total completions and exposes more work instead of an empty result. */
 init();assert(!xnf_send(&n,packet,64));tx(0,n.tx->ring[0].req.id,0);
 stream=true;backend_receive();assert(xnf_pending(&n));
 assert(!xnf_poll(&n)&&n.stats.tx+n.stats.rx==64&&n.stats.tx==1&&xnf_pending(&n));
 stream=false;assert(!xnf_poll(&n)&&!xnf_pending(&n)&&xnf_drained(&n));cases++;
 printf("production netif ring: %u cases, 70000-frame descriptor/ID wrap loop PASS\n",cases);
 return 0;
}
