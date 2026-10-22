/* SPDX-License-Identifier: Apache-2.0 */
#include <xenova/grant_pool.h>
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
static struct xgp_entry table[512];static struct xgp_pool p;
int main(void) {
 unsigned cases=0;uint32_t ref,refs[XGP_COUNT];
 assert(xgp_share(NULL,0,100,false,&ref)==-EINVAL);cases++;
 p.table=table;table[0].flags=0x1234;
 for(unsigned i=0;i<XGP_COUNT;i++){
  assert(!xgp_share(&p,0,100+i,i&1,&refs[i]));
  assert(refs[i]==XGP_FIRST+i);
  assert(table[refs[i]].frame==100+i&&table[refs[i]].flags==(XGP_PERMIT|((i&1)?XGP_READONLY:0)));
 }
 assert(table[0].flags==0x1234);cases++;
 assert(xgp_share(&p,0,100,false,&ref)==-ENOSPC);cases++;
 assert(xgp_revoke(&p,7)==-EINVAL&&xgp_revoke(&p,40)==-EINVAL);cases++;
 for(unsigned flag=XGP_READING;flag<=XGP_WRITING;flag<<=1){
  table[8].flags=XGP_PERMIT|flag;
  assert(xgp_revoke(&p,8)==-EBUSY&&p.held[0]);
  assert(xgp_share(&p,0,999,false,&ref)==-ENOSPC);cases++;
 }
 table[8].flags=XGP_PERMIT|XGP_READING|XGP_WRITING;
 assert(xgp_revoke(&p,8)==-EBUSY&&p.held[0]);cases++;
 table[8].flags=XGP_PERMIT;
 assert(!xgp_revoke(&p,8)&&table[8].flags==0&&!p.held[0]);cases++;
 assert(xgp_revoke(&p,8)==-ENOENT);cases++;
 assert(!xgp_share(&p,17,999,true,&ref)&&ref==8&&table[8].domid==17&&table[8].frame==999);cases++;
 for(unsigned i=0;i<XGP_COUNT;i++){assert(!xgp_revoke(&p,refs[i]));}
 cases++;
 for(unsigned i=0;i<100000;i++){
  assert(!xgp_share(&p,0,i,false,&ref));assert(!xgp_revoke(&p,ref));
 }
 cases++;
 printf("production grant pool: %u bounded ownership/revocation cases, 100000 recycle loop PASS\n",cases);
}
