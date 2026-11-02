/* SPDX-License-Identifier: Apache-2.0 */
#include <xenova/resources.h>
#include <errno.h>
#include <string.h>
int xdr_percent(uint64_t n,uint64_t d,uint32_t *out)
{
 if(!out||!d)return -ENODATA;
 if(n>d)return -ERANGE;
 /* Binary long division of n/d to five decimal places without n*100000
  * overflow. The residual doubles only via subtraction when near UINT64_MAX. */
 uint64_t rem=n%d;uint32_t result=n==d?100000:0;
 if(n!=d){
  for(unsigned i=0;i<5;i++){
   unsigned digit=0;uint64_t next=0;
   for(unsigned j=0;j<10;j++){
    if(next>=d-rem){next-=d-rem;digit++;}else next+=rem;
   }
   result=result*10+digit;rem=next;
  }
  if(rem>=d-rem)result++;
 }
 *out=result;return 0;
}
void xdr_window(const struct xdr_counter *p,const struct xdr_counter *n,
 uint64_t max_gap,struct xdr_window *w)
{
 memset(w,0,sizeof(*w));w->end_ms=n->at_ms;w->start_ms=p?p->at_ms:n->at_ms;
 if(!p){w->rc=-ENODATA;return;}
 if(n->at_ms<=p->at_ms||n->at_ms-p->at_ms>=max_gap||n->total<p->total||n->busy<p->busy){w->rc=-EOVERFLOW;return;}
 w->total_delta=n->total-p->total;w->busy_delta=n->busy-p->busy;
 w->rc=xdr_percent(w->busy_delta,w->total_delta,&w->percent_milli);
}
