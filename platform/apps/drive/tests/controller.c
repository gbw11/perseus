/* Product controller + unchanged manual/auto calculations, host sanitizer test. */
#include "controller.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#define A "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
#define B "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
static struct xm_span span(const char *s){return (struct xm_span){s,strlen(s)};}
static const char *input="{\"keys\":[\"up\"]}";
static const char *perception="{\"lane_offset_m\":0,\"heading_err_rad\":0,\"stop\":false,\"obstacles\":[]}";
int main(void){
 for(int swapped=0;swapped<2;swapped++){
  const char *own=swapped?B:A,*other=swapped?A:B;
  struct drive_controller c;struct drive_result r;char cp[256];drive_controller_init(&c);
  for(unsigned gen=1;gen<=5;gen++){
   const char *mode=gen==2?"MANUAL":gen==3?"AUTO_READY":"AUTO";
   const char *app=(gen==2||gen==3)?"manual":"autonomous";
   const char *active=gen==5?other:own;
   snprintf(cp,sizeof(cp),"{\"generation\":%u,\"mode\":\"%s\",\"active_app\":\"%s\",\"active_vm_id\":\"%s\"}",gen,mode,app,active);
   drive_controller_step(&c,own,span(cp),span(input),0,span(perception),0,&r);
   assert(r.control_valid&&r.generation==gen&&r.mode==(gen==2||gen==3?DRIVE_MANUAL:DRIVE_AUTO));
   assert(r.locally_active==(gen!=5));
   assert(r.v_mps>0);
  }
  drive_controller_step(&c,own,span(cp),span(input),999,span(perception),999,&r);
  assert(r.v_mps==0&&!strcmp(r.auto_state,"error")&&!strcmp(r.manual_state,"error"));
  drive_controller_step(&c,own,span("{\"generation\":4,\"mode\":\"AUTO\"}"),span(input),0,span(perception),0,&r);
  assert(!r.control_valid&&r.mode==DRIVE_STOP);
  snprintf(cp,sizeof(cp),"{\"generation\":5,\"mode\":\"MANUAL\",\"active_app\":\"manual\",\"active_vm_id\":\"%s\"}",own);
  drive_controller_step(&c,own,span(cp),span(input),0,span(perception),0,&r);
  assert(!r.control_valid); /* same generation cannot change selection */
  drive_controller_step(&c,own,span("{\"generation\":6,\"mode\":\"AUTO\",\"active_app\":\"autonomous\"}"),span(input),0,span(perception),0,&r);
  assert(!r.control_valid);
  drive_controller_step(&c,own,span("{\"generation\":6,\"mode\":\"SAFE_STOP\",\"active_app\":null,\"active_vm_id\":null}"),span(input),0,span(perception),0,&r);
  assert(r.control_valid&&r.mode==DRIVE_STOP&&r.v_mps==0);
 }
 puts("common controller: both modes, symmetric UUID, expiry, generation, SAFE_STOP PASS");
}
