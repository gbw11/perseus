/* SPDX-License-Identifier: Apache-2.0 */
#include <xenova/shutdown_flow.h>
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
static int64_t ticks;static int fail_at,fail_code,expire_at;static unsigned order,reason;
static int64_t now(void *p){(void)p;return ticks;}
static int step(unsigned expected){
 assert(++order==expected);
 if((int)expected==expire_at)ticks=100;
 return (int)expected==fail_at?fail_code:0;
}
static int ack(void *p,int64_t e){(void)p;assert(e==100);return step(1);}
static int hook(void *p,const char *r,int64_t e){(void)p;assert(!strcmp(r,"reboot")||!strcmp(r,"poweroff"));assert(e==100);return step(2);}
static int net(void *p,int64_t e){(void)p;assert(e==100);return step(3);}
static int store(void *p,int64_t e){(void)p;assert(e==100);return step(4);}
static int hyper(void *p,unsigned r){(void)p;reason=r;return step(5);}
static struct xsd_flow init(void){
 ticks=0;fail_at=expire_at=0;order=reason=0;
 return (struct xsd_flow){.ops={now,ack,hook,net,store,hyper,NULL}};
}
int main(void){
 unsigned cases=0;
 struct xsd_flow f=init();assert(xsd_run(&f,"suspend",100)==-ENOTSUP&&order==0&&f.phase==XSD_IDLE);cases++;
 for(unsigned r=0;r<2;r++){
  f=init();assert(xsd_run(&f,r?"reboot":"poweroff",100)==-EIO);
  assert(order==5&&reason==r&&f.phase==XSD_HYPERCALL_RETURNED);cases++;
  assert(xsd_run(&f,"reboot",100)==-EALREADY&&order==5);cases++;
 }
 for(int stage=1;stage<=5;stage++){
  f=init();fail_at=stage;fail_code=-EIO;assert(xsd_run(&f,"poweroff",100)==-EIO&&order==(unsigned)stage);cases++;
  assert(xsd_run(&f,"poweroff",100)==-EALREADY);cases++;
 }
 f=init();fail_at=2;fail_code=-EBUSY;assert(xsd_run(&f,"poweroff",100)==-EBUSY&&f.phase==XSD_REJECTED&&order==2);cases++;
 f=init();fail_at=2;fail_code=-ETIMEDOUT;assert(xsd_run(&f,"poweroff",100)==-ETIMEDOUT&&f.phase==XSD_TIMED_OUT&&order==2);cases++;
 for(int stage=1;stage<=4;stage++){
  f=init();expire_at=stage;assert(xsd_run(&f,"reboot",100)==-ETIMEDOUT&&order==(unsigned)stage&&f.phase==XSD_TIMED_OUT);cases++;
 }
 f=init();ticks=100;assert(xsd_run(&f,"poweroff",100)==-ETIMEDOUT&&order==0);cases++;
 printf("production shutdown flow: %u ordering/deadline/failure/replay cases PASS\n",cases);
}
