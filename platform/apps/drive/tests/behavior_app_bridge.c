/* Native integration fixture: actual planner/app/input/lifecycle/serializers.
 * Kernel scheduling, authenticated transport, bootstrap and plant are explicit
 * leaves. It never assigns confirmed clock fields or manufactures READY. */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "lattice_app.c"
#include "lifecycle.h"
#include "input_bridge.h"

static struct xt_bootstrap bridge_boot;
static struct xd_inputs bridge_inputs;
static struct drive_controller bridge_controller;
static struct drive_completion bridge_completion;
static struct drive_task_status bridge_status={.state=DRIVE_TASK_WAITING,.lifetime=1,.started=true};
static uint64_t clock_origin,clock_base=1000000,context_revision=1,check_sequence;
static uint8_t bridge_wire[DW_WIRE_MAX];
static char bridge_line[DW_WIRE_MAX*2+256];
/* Native sink for the existing product diagnostics. One process-lifetime cap;
 * no new getter, success state or permission is manufactured by observation. */
void b08_diagnostic_log(const char *format,...){
 static unsigned count;if(count>=64)return;count++;
 fputs("B08_DIAG ",stderr);va_list arguments;va_start(arguments,format);vfprintf(stderr,format,arguments);va_end(arguments);fputc('\n',stderr);
}
static uint64_t wall_us(void){struct timespec t;assert(!clock_gettime(CLOCK_MONOTONIC,&t));return (uint64_t)t.tv_sec*1000000+(uint64_t)t.tv_nsec/1000;}
int xdiag_clock_us(uint64_t *out){*out=clock_base+wall_us()-clock_origin;return 0;}
int64_t k_uptime_get(void){uint64_t now;xdiag_clock_us(&now);return (int64_t)(now/1000);}
int xd_context_get(struct xt_bootstrap *out){*out=bridge_boot;return 0;}
bool xd_stopping(void){return false;}
bool drive_task_stopping(enum drive_task task){(void)task;return false;}
int k_mutex_lock(struct k_mutex *m,int wait){(void)m;(void)wait;return 0;}
void k_mutex_unlock(struct k_mutex *m){(void)m;}
void k_sleep(int64_t delay){(void)delay;}
void k_yield(void){}
void sys_rand_get(void *out,size_t size){
 static uint64_t sequence;uint8_t *p=out;sequence++;
 for(size_t k=0;k<size;k++)p[k]=(uint8_t)(bridge_boot.identity.vm_id[k%36]^(unsigned)(sequence+13*k));
}
struct k_thread *k_thread_create(struct k_thread *t,void *s,size_t n,void (*f)(void *,void *,void *),void *a,void *b,void *c,int p,int flags,int d){
 (void)t;(void)s;(void)n;(void)f;(void)a;(void)b;(void)c;(void)p;(void)flags;(void)d;return NULL;
}
int k_thread_join(struct k_thread *t,int64_t deadline){(void)t;(void)deadline;return 0;}
void k_thread_name_set(struct k_thread *t,const char *name){(void)t;(void)name;}
int xd_io_binary(const struct xm_zbinary *binary){(void)binary;return 0;}
int xm_zclient_init(struct xm_zclient *c,const char *vm,const char *events,const char *app,const struct xm_zconfig *config){
 (void)vm;(void)events;(void)app;(void)config;memset(c,0,sizeof(*c));c->socket_open=c->connected=true;return 0;
}
void xm_zclient_close(struct xm_zclient *c){c->socket_open=c->connected=false;}
int xm_zclient_subscribe(struct xm_zclient *c,const char *topic,int qos){(void)c;(void)topic;(void)qos;return 0;}
int xm_zclient_poll(struct xm_zclient *c,int timeout){(void)c;(void)timeout;return 0;}
static void print_hex(const void *data,size_t count){const uint8_t *p=data;for(size_t k=0;k<count;k++)printf("%02x",p[k]);}
int mqtt_publish(struct mqtt_client *c,const struct mqtt_publish_param *p){
 (void)c;printf("WIRE %.*s ",(int)p->message.topic.topic.size,p->message.topic.topic.utf8);
 print_hex(p->message.payload.data,p->message.payload.len);putchar('\n');return 0;
}
static int unhex(const char *text,uint8_t *out,size_t cap){
 size_t n=strcspn(text,"\r\n");if(n%2||n/2>cap)return -EINVAL;
 for(size_t k=0;k<n/2;k++){unsigned value;if(sscanf(text+2*k,"%2x",&value)!=1)return -EINVAL;out[k]=(uint8_t)value;}return (int)(n/2);
}
static void lifecycle(void){
 struct drive_result selected;struct xm_span control={bridge_inputs.control.payload,(uint16_t)bridge_inputs.control.length};
 drive_controller_select(&bridge_controller,bridge_boot.identity.vm_id,control,&selected);
 uint64_t now;xdiag_clock_us(&now);bool chosen=selected.locally_active&&selected.mode==DRIVE_AUTO;
 struct drive_warm_view view=drive_warm_classify(&bridge_controller.automatic,&selected,&bridge_status,&bridge_completion,chosen,
  (struct xm_span){NULL,0},0,context_revision,1,(int64_t)(now/1000),now,true);
 char body[256];int n=xm_app_lifecycle_json(body,sizeof(body),view.generation,++check_sequence,bridge_completion.progress,view.phase,view.state,view.reason);
 if(n>0)printf("LIFECYCLE %s\n",body);
}
static int calculate(uint64_t sequence){
 struct drive_result selected;struct xm_span control={bridge_inputs.control.payload,(uint16_t)bridge_inputs.control.length};
 drive_controller_select(&bridge_controller,bridge_boot.identity.vm_id,control,&selected);
 if(!selected.control_valid||!selected.locally_active||selected.mode!=DRIVE_AUTO)return -EACCES;
 memset(&bridge_completion,0,sizeof(bridge_completion));struct drive_completion *w=&bridge_completion;
 xdiag_clock_us(&w->begin_us);w->source.lifetime=1;w->source.context_revision=w->context_revision=context_revision;w->source.connection_revision=1;
 w->source.sequence=w->sequence=sequence;strcpy(w->source.vm_id,bridge_boot.identity.vm_id);strcpy(w->source.app_instance,bridge_boot.auto_instance_id);
 w->source.control_length=control.len;memcpy(w->source.control,control.p,control.len);
 w->source.release_us=w->begin_us;w->source.deadline_us=w->begin_us+20000;w->performed=true;
 w->rc=drive_adapter_run_until(&bridge_controller.automatic,control,(struct xm_span){NULL,0},0,NULL,NULL,w->source.deadline_us,&w->sample);
 xdiag_clock_us(&w->end_us);w->finished_ms=(int64_t)(w->end_us/1000);w->progress=bridge_controller.automatic.progress;
 drive_lattice_completed(w);struct xm_drive_command3 command;uint64_t until=0;
 int rc=drive_lattice_command(w,&command,&until);if(rc)return rc;
 char body[XM_COMMAND2_BYTES+1];int n=xm_drive_command3_json(body,sizeof(body),&command);if(n<0)return n;
 drive_lattice_command_attempt(w,&command);printf("COMMAND %llu %s\n",(unsigned long long)until,body);return 0;
}
int main(void){
 clock_origin=wall_us();setvbuf(stdout,NULL,_IOLBF,0);
 de_init(&engine,&lp_selected_config,&lp_selected_assets);drive_controller_init(&bridge_controller);
 drive_lattice_adapter(&bridge_controller.automatic,false);drive_lattice_adapter(&bridge_controller.manual,true);
 assert(!b08_input_bind(&engine));
 while(fgets(bridge_line,sizeof(bridge_line),stdin)){
  char *argument=strchr(bridge_line,' ');if(argument)*argument++=0;else bridge_line[strcspn(bridge_line,"\r\n")]=0;
  int rc=0;
  if(!strcmp(bridge_line,"boot")){
   assert(argument&&sscanf(argument,"%36s %36s %36s %36s %36s %36s",bridge_boot.identity.vm_id,bridge_boot.identity.execution_id,
    bridge_boot.auto_instance_id,bridge_boot.identity.host_boot_id,bridge_boot.identity.boot_id,bridge_inputs.control.instance)==6);
   strcpy(bridge_boot.world_profile,lp_selected_config.id);strcpy(bridge_boot.world_broker_ipv4,"127.0.0.1");strcpy(bridge_boot.broker_ipv4,"127.0.0.1");
   bridge_boot.port=bridge_boot.world_port=1883;strcpy(bridge_inputs.vm_id,bridge_boot.identity.vm_id);strcpy(bridge_inputs.app_instances[0],bridge_boot.auto_instance_id);
   strcpy(bridge_inputs.control.sender,"dom0-gateway");bridge_inputs.connected=true;sys_rand_get(behavior_boot_nonce,sizeof(behavior_boot_nonce));
  }else if(!strcmp(bridge_line,"control")){
   rc=argument?unhex(argument,bridge_wire,sizeof(bridge_wire)):-EINVAL;
   if(rc>0&&(size_t)rc<=XM_MAX_DRIVING_BYTES){bridge_inputs.control.length=(size_t)rc;memcpy(bridge_inputs.control.payload,bridge_wire,(size_t)rc);
    bridge_inputs.control.payload[rc]=0;context_revision++;drive_lattice_context(&bridge_inputs);rc=0;}
  }else if(!strcmp(bridge_line,"link")||!strcmp(bridge_line,"world")||!strcmp(bridge_line,"vehicle")){
   int n=argument?unhex(argument,bridge_wire,sizeof(bridge_wire)):-EINVAL;
   rc=n<0?n:!strcmp(bridge_line,"link")?b08_input_link(bridge_wire,(size_t)n):!strcmp(bridge_line,"world")?
    b08_input_world(bridge_wire,(size_t)n):b08_input_vehicle(bridge_wire,(size_t)n);
  }else if(!strcmp(bridge_line,"step")){
   unsigned count=argument?(unsigned)strtoul(argument,NULL,10):1;assert(count&&count<=2048);
   for(unsigned k=0;k<count;k++){b08_input_map_step();(void)planner_step();b08_input_step();}
  }else if(!strcmp(bridge_line,"io")){b08_input_map_step();b08_input_step();}
  else if(!strcmp(bridge_line,"calc"))rc=argument?calculate(strtoull(argument,NULL,10)):-EINVAL;
  else if(!strcmp(bridge_line,"life"))lifecycle();
  else if(!strcmp(bridge_line,"meta")){
   struct dl_clock mapping={0};uint64_t pi_now=0;int mapped=dl_clock_get(&mapping,&pi_now);
   printf("META {\"behavior\":%d,\"planning\":%d,\"prepared\":%d,\"phase\":%u,\"parking\":%u,\"request\":%d,\"reason\":%d,\"engine_bytes\":%u,\"state_bytes\":%u,\"clock_reply_bytes\":%u,"
    "\"clock_rc\":%d,\"clock_acknowledged\":%d,\"clock_probe_sequence\":%llu,\"clock_expires_guest\":%llu,\"pi_now\":%llu,"
    "\"map_current\":%d,\"maps_published\":%llu,\"receive_awaiting\":%d,\"last_world_result\":%d,\"requested\":%u,\"started\":%u,\"path_current\":%d,"
    "\"last_reason\":%d,\"last_reason_name\":\"%s\",\"search_reason\":%d,\"search_reason_name\":\"%s\",\"has_grant\":%d,\"facts_current\":%d,\"episode_state\":%u}\n",
    engine.behavior_enabled,engine.planning,(int)atomic_load(&prepared_valid),engine.episode.phase,engine.parking.phase,engine.episode_request_ready||engine.episode_receipt_pending,
    engine.episode.reason,(unsigned)sizeof(engine),(unsigned)sizeof(dw_state),(unsigned)sizeof(xenova_drive_ClockReply),
    mapped,mapping.acknowledged,(unsigned long long)mapping.probe_sequence,(unsigned long long)mapping.expires_guest,(unsigned long long)pi_now,
    engine.maps.current,(unsigned long long)engine.maps.published,engine.receive_awaiting,atomic_load(&engine.last_world_result),atomic_load(&engine.requested),
    engine.started,engine.current,atomic_load(&engine.last_reason),de_reason(atomic_load(&engine.last_reason)),atomic_load(&engine.search_reason),
    lp_reason(atomic_load(&engine.search_reason)),engine.has_grant,(int)atomic_load(&engine.facts_current),engine.episode_state.core.state);
  }else if(!strcmp(bridge_line,"quit")){atomic_set(&stop,1);atomic_set(&planner_stop,1);de_stop(&engine);
   while(engine.planning)(void)de_plan_slice(&engine,1,0);
   while(engine.apply_pending)(void)de_apply_finish(&engine,0);
   if(planner_turn.slot>=0)dl_state_release(planner_turn.slot);
   printf("END 0 0\n");break;
  }else rc=-EINVAL;
  uint64_t now;xdiag_clock_us(&now);printf("END %d %llu\n",rc,(unsigned long long)now);
 }
 return 0;
}
