/* Shared production payload entry points must preserve each caller's input.
 * No transport, parser mock or lock serializes the concurrent callers. */
#define _GNU_SOURCE
#include <xenova/mqtt/xm.h>
#include <assert.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static pthread_barrier_t start;
static const char *const payloads[]={
 "{\"generation\":1,\"mode\":\"AUTO\",\"active_app\":\"autonomous\",\"active_vm_id\":\"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa\"}",
 "{\"active_vm_id\":\"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb\",\"mode\":\"MANUAL\",\"generation\":4294967295,\"active_app\":\"manual\"}",
 "{\"keys\":[\"up\",\"left\"]}","{\"keys\":[\"brake\",\"right\",\"down\"]}",
 "{\"lane_offset_m\":0.125,\"heading_err_rad\":0,\"stop\":false,\"obstacles\":[]}",
 "{\"obstacles\":[{\"x\":1,\"y\":2,\"w\":3}],\"stop\":true,\"heading_err_rad\":0,\"lane_offset_m\":-0.75}"
};
static void *parse(void *argument)
{
 uintptr_t id=(uintptr_t)argument;
 struct xm_span payload={payloads[id],strlen(payloads[id])};
 (void)pthread_barrier_wait(&start);
 for(unsigned n=0;n<20000;n++){
  if(id<2){
   struct xm_vehicle_control out;
   assert(!xm_vehicle_control_parse(payload,&out));
   assert(out.generation==(id?UINT32_MAX:1));
   assert(!strcmp(out.mode,id?"MANUAL":"AUTO"));
   assert(!strcmp(out.active_app,id?"manual":"autonomous"));
   assert(!strcmp(out.active_vm_id,id?"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"));
  }else if(id<4){
   uint32_t keys;
   assert(!xm_drive_input_parse(payload,&keys));
   assert(keys==(id==2?(XM_KEY_UP|XM_KEY_LEFT):(XM_KEY_BRAKE|XM_KEY_RIGHT|XM_KEY_DOWN)));
  }else{
   struct xm_perception_result out;
   assert(!xm_perception_result_parse(payload,&out));
   assert(out.lane_offset_m==(id==4?.125f:-.75f));
   assert(out.stop==(id==5)&&out.n_obstacles==(id==5?1:0));
  }
 }
 return NULL;
}
int main(void)
{
 pthread_t threads[6];
 assert(!pthread_barrier_init(&start,NULL,6));
 for(uintptr_t i=0;i<6;i++)assert(!pthread_create(&threads[i],NULL,parse,(void *)i));
 for(unsigned i=0;i<6;i++)assert(!pthread_join(threads[i],NULL));
 assert(!pthread_barrier_destroy(&start));
 puts("PASS 120000 concurrent production payload parses");
}
