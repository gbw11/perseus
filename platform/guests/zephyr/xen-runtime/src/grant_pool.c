/* SPDX-License-Identifier: Apache-2.0 */
/* Caller serializes this fixed pool. Busy entries are NEVER freed or reused. */
#include <xenova/grant_pool.h>
#include <errno.h>
int xgp_share(struct xgp_pool *p,uint16_t dom,uint32_t frame,bool ro,uint32_t *out) {
 if(!p||!p->table||!out)return -EINVAL;
 unsigned i=0;while(i<XGP_COUNT&&p->held[i])i++;
 if(i==XGP_COUNT)return -ENOSPC;
 p->held[i]=true;uint32_t ref=XGP_FIRST+i;
 p->table[ref].domid=dom;p->table[ref].frame=frame;
 __atomic_store_n(&p->table[ref].flags,XGP_PERMIT|(ro?XGP_READONLY:0),__ATOMIC_RELEASE);
 *out=ref;return 0;
}
int xgp_revoke(struct xgp_pool *p,uint32_t ref) {
 if(!p||!p->table||ref<XGP_FIRST||ref>=XGP_FIRST+XGP_COUNT)return -EINVAL;
 if(!p->held[ref-XGP_FIRST])return -ENOENT;
 uint16_t flags=__atomic_load_n(&p->table[ref].flags,__ATOMIC_ACQUIRE);
 for(unsigned attempt=0;attempt<8;attempt++) {
  if(flags&(XGP_READING|XGP_WRITING))return -EBUSY;
  if(__atomic_compare_exchange_n(&p->table[ref].flags,&flags,0,false,__ATOMIC_SEQ_CST,__ATOMIC_ACQUIRE)) {
   p->held[ref-XGP_FIRST]=false;return 0;
  }
 }
 return -EBUSY;
}
