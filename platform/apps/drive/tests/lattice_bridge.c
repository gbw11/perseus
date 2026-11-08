/* SPDX-License-Identifier: Apache-2.0 */
/* Host test/experiment ABI only; firmware never links allocation or this ABI. */
#include "lattice.h"
#include "engine.h"
#include "pb_encode.h"
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <math.h>

extern const struct lp_config lp_selected_config;
extern const struct lp_assets lp_selected_assets;
struct probe {struct dw_snapshot snapshot;struct lp_search search;struct lp_path path;
 struct lp_tracker tracker;struct lp_config config;struct lp_metrics tracked;
 struct lp_collision_index collision_index;struct tr_basis basis;uint64_t cancel_after;bool cancelled;enum dw_result decoded;};
static const struct dw_trust trust={.vehicle="rc-test-1",.mapper_boot="11111111-1111-4111-8111-111111111111",
 .mapper_instance="22222222-2222-4222-8222-222222222222",.map_epoch="33333333-3333-4333-8333-333333333333",
 .frame_epoch="44444444-4444-4444-8444-444444444444",.odom_epoch="55555555-5555-4555-8555-555555555555",
 .configuration="lattice-lab-1",.calibration="encoder-lab-1",.primitive=TEST_PRIMITIVE_SHA,
 .clock={.epoch="66666666-6666-4666-8666-666666666666",.lower_offset_us=100000,
 .upper_offset_us=101000,.expires_pi_us=3000000,.max_error_us=2000},.max_live_age_us=300000};
static int clock_us(void *unused,uint64_t *out) {
 (void)unused;struct timespec t;if(clock_gettime(CLOCK_MONOTONIC,&t))return -1;
 *out=(uint64_t)t.tv_sec*1000000+(uint64_t)t.tv_nsec/1000;return 0;
}
static bool cancelled(void *context) {
 struct probe *p=context;return p->cancelled||(p->cancel_after&&p->search.metrics.footprints>=p->cancel_after);
}
void *probe_new(const uint8_t *bytes,size_t length) {
 struct probe *p=calloc(1,sizeof(*p));if(!p)return NULL;
 p->decoded=dw_decode(bytes,length,&trust,1200000,&p->snapshot);p->config=lp_selected_config;
 if(!p->decoded)lp_index_build(&p->collision_index,&p->snapshot.world,&p->config);
 return p;
}
void probe_free(void *pointer) { free(pointer); }
int probe_decoded(void *pointer) { return ((struct probe *)pointer)->decoded; }
int probe_plan(void *pointer,float x,float y,float yaw,float steer,float gx,float gy,float gyaw,
 unsigned nodes,unsigned collisions,uint64_t cancel_after,unsigned budget_ms,float width) {
 struct probe *p=pointer;if(p->decoded)return LP_INPUT;
 if(nodes)p->config.node_limit=nodes;
 if(collisions)p->config.collision_limit=collisions;
 if(width>0)p->config.half_width=width;
 p->cancel_after=cancel_after;uint64_t now;
 if(clock_us(NULL,&now))return LP_CLOCK;
 struct lp_budget b={now+(uint64_t)budget_ms*1000,clock_us,cancelled,p,&p->collision_index};
 enum lp_result rc=lp_start(&p->search,&p->snapshot.world,(dw_pose){x,y,yaw},steer,(dw_pose){gx,gy,gyaw},
                           &p->config,&lp_selected_assets,b,0);
 while(rc==LP_SEARCHING)rc=lp_advance(&p->search,8,&p->path);
 return rc;
}
unsigned probe_count(void *pointer) { return (unsigned)((struct probe *)pointer)->path.count; }
int probe_direction(void *pointer) { return ((struct probe *)pointer)->path.direction; }
int probe_point(void *pointer,unsigned index,float *out) {
 struct probe *p=pointer;if(index>=p->path.count)return -1;
 const xenova_drive_PathPoint *v=&p->path.points[index];
 out[0]=v->pose.x;out[1]=v->pose.y;out[2]=v->pose.yaw;out[3]=v->steering_rad;out[4]=v->velocity_mps;return 0;
}
int probe_track(void *pointer,float x,float y,float yaw,float velocity,float steer,float dt,float *out) {
 struct probe *p=pointer;dw_vehicle vehicle={.quality=3,.velocity_mps=velocity,.has_estimated_steering_rad=true,
  .estimated_steering_rad=steer,.position_error_m=.005f,.heading_error_rad=.01f,.has_steering_error_rad=true,.steering_error_rad=.001f};
 struct lp_command cmd;struct lp_metrics metrics={0};uint64_t now;
 if(clock_us(NULL,&now))return LP_CLOCK;
 struct lp_budget b={now+1000000,clock_us,NULL,NULL,&p->collision_index};
 enum lp_result rc=lp_track(&p->tracker,&p->path,&p->snapshot.world,(dw_pose){x,y,yaw},&vehicle,
                          &p->config,dt,&cmd,&metrics,&b);
 p->tracked.cells+=metrics.cells;p->tracked.footprints+=metrics.footprints;
 out[0]=cmd.velocity;out[1]=cmd.steer;out[2]=cmd.target_x;out[3]=cmd.target_y;
 out[4]=cmd.geometric_distance;out[5]=cmd.curvature;out[6]=cmd.known_distance;out[7]=cmd.emergency;
 return rc;
}
void probe_metrics(void *pointer,uint64_t *out) {
 struct probe *p=pointer;const struct lp_metrics *m=&p->search.metrics;
 out[0]=m->expanded;out[1]=m->primitives;out[2]=m->footprints;out[3]=m->cells;
 out[4]=m->nodes_high_water;out[5]=m->open_high_water;out[6]=m->index_probes_max;
 out[7]=m->cancel_checks;out[8]=m->clock_checks;out[9]=sizeof(*p);out[10]=p->tracked.cells;
}
int probe_update(void *pointer,const uint8_t *bytes,size_t n) {
 struct probe *p=pointer;struct dw_snapshot *next=malloc(sizeof(*next));if(!next)return -1;
 enum dw_result rc=dw_decode(bytes,n,&trust,1200000,next);
 if(!rc){p->snapshot=*next;lp_index_build(&p->collision_index,&p->snapshot.world,&p->config);}
 free(next);return rc;
}
int probe_valid(void *pointer) {
 struct probe *p=pointer;struct lp_metrics m={0};uint64_t now;
 if(clock_us(NULL,&now))return LP_CLOCK;
 struct lp_budget b={now+1000000,clock_us,NULL,NULL,&p->collision_index};
 return lp_validate_path(&p->path,&p->snapshot.world,&p->config,&m,&b);
}
void *engine_new(void){
 struct drive_engine *e=calloc(1,sizeof(*e));if(!e)return NULL;
 de_init(e,&lp_selected_config,&lp_selected_assets);dw_pool_source(&e->maps,&trust);return e;
}
void engine_free(void *e){free(e);}
int engine_world(void *e,const uint8_t *bytes,size_t n,uint64_t now){return de_world(e,bytes,n,now);}
int engine_vehicle(void *e,const uint8_t *bytes,size_t n){
 dw_vehicle vehicle;enum dw_result rc=dw_vehicle_decode(bytes,n,&vehicle);
 return rc?100+(int)rc:(int)de_vehicle(e,&vehicle);
}
int engine_plan(void *pointer,uint64_t now,unsigned slices){
 struct drive_engine *e=pointer;uint64_t clock;if(clock_us(NULL,&clock))return DE_CLOCK;
 enum de_result received=de_receive_poll(e,now);
 if(e->maps.current<0&&received)return received;
 struct lp_budget b={clock+10000000,clock_us,NULL,NULL,NULL};
 enum de_result rc=de_plan_start(e,now,b);
 if(rc)return rc;
 for(unsigned k=0;k<slices&&e->planning;k++)rc=de_plan_slice(e,8,now);
 return rc;
}
int engine_track(void *e,uint64_t now,float *out){
 (void)de_receive_poll(e,now); /* Fixture dispatches the product map owner. */
 uint64_t clock;if(clock_us(NULL,&clock))return DE_CLOCK;
 struct lp_command command;dw_vehicle vehicle;struct lp_budget b={clock+1000000,clock_us,NULL,NULL,NULL};
 enum de_result rc=de_track(e,now,.02f,&command,&vehicle,b);
 out[0]=command.velocity;out[1]=command.steer;out[2]=command.emergency;out[3]=(float)command.result;
 return rc;
}
int engine_apply(void *e,const uint8_t *bytes,size_t n,uint64_t now){
 (void)de_receive_poll(e,now);
 dw_state *s=malloc(sizeof(*s));if(!s)return -1;enum dw_result rc=dw_state_decode(bytes,n,s);
 if(rc){free(s);return 100+(int)rc;}
 uint64_t clock;if(clock_us(NULL,&clock)){free(s);return DE_CLOCK;}
 struct lp_budget b={clock+10000000,clock_us,NULL,NULL,NULL};
 enum de_result result=de_apply(e,s,now,b);free(s);return result;
}
int engine_grant(void *e,const uint8_t *bytes,size_t n,uint64_t now){
 xenova_drive_DriveLink *link=calloc(1,sizeof(*link));if(!link)return -1;
 enum dw_result rc=dw_link_decode(bytes,n,link);
 int result=rc?100+(int)rc:link->which_payload!=xenova_drive_DriveLink_mission_grant_tag?DE_STATE:
  (int)de_mission(e,&link->payload.mission_grant,now);
 free(link);return result;
}
int engine_track_grant(void *e,const uint8_t *bytes,size_t n,uint64_t now,float *out){
 xenova_drive_DriveLink *link=calloc(1,sizeof(*link));if(!link)return -1;
 enum dw_result decoded=dw_link_decode(bytes,n,link);uint64_t clock;
 if(decoded||link->which_payload!=xenova_drive_DriveLink_mission_grant_tag||clock_us(NULL,&clock)){free(link);return DE_STATE;}
 struct lp_command command;dw_vehicle vehicle;struct lp_budget budget={clock+1000000,clock_us,NULL,NULL,NULL};
 enum de_result rc=de_track_mission(e,&link->payload.mission_grant,now,.02f,&command,&vehicle,budget);
 out[0]=command.velocity;out[1]=command.steer;free(link);return rc;
}
int engine_state(void *pointer,uint8_t *out,size_t cap){
 struct drive_engine *e=pointer;struct de_reference h;const struct lp_path *p;
 if(de_path_acquire(e,&h,&p))return -1;
 dw_state *s=calloc(1,sizeof(*s));if(!s){de_path_release(e,h);return -1;}
 if(!lp_state_export(p,s)){free(s);(void)de_path_release(e,h);return -1;}strcpy(s->source_vm,"77777777-7777-4777-8777-777777777777");
 strcpy(s->source_execution,"88888888-8888-4888-8888-888888888888");strcpy(s->source_app_instance,"99999999-9999-4999-8999-999999999999");
 s->source_generation=1;s->state_revision=1;strcpy(s->frame_epoch,p->frame);strcpy(s->odom_epoch,p->odom);
 strcpy(s->configuration,p->configuration);strcpy(s->primitive,e->assets->sha256);strcpy(s->reference_id,p->reference);
 s->reference_revision=e->last_track_world.reference_revision?e->last_track_world.reference_revision:1;
 s->path_revision=p->revision;s->maneuver_revision=p->maneuver_revision;
 s->has_world_features=true;s->world_features=p->world_features;
 s->direction=p->direction;s->maneuver_phase=p->phase;s->merge_goal=p->merge_goal;
 if(p->merge_passed_reference_revision&&p->merge_passed_reference_revision==s->reference_revision){
  s->has_merge_passed_reference_revision=true;s->merge_passed_reference_revision=p->merge_passed_reference_revision;
 }
 s->vehicle_sequence=2;s->calculation_sequence=e->completed;s->tracker_index=e->tracker.index;s->complete=true;
 if(s->has_mission)s->mission.active_segment=e->tracker.segment;
 strcpy(s->message_id,"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");strcpy(s->calibration,"encoder-lab-1");
 strcpy(s->map_epoch,"33333333-3333-4333-8333-333333333333");s->observation_seq=1;
 s->path_count=p->count;memcpy(s->path,p->points,p->count*sizeof(*p->points));de_path_release(e,h);
 pb_ostream_t stream=pb_ostream_from_buffer(out,cap);bool ok=pb_encode(&stream,xenova_drive_DriveState_fields,s);
 free(s);return ok?(int)stream.bytes_written:-1;
}

int engine_memory(void *e,unsigned region,uint64_t *out){
 struct dw_memory m;enum de_result rc=de_memory_read(e,region,&m);
 if(!rc){out[0]=m.reserved;out[1]=m.capacity;out[2]=m.used;out[3]=m.peak;out[4]=m.exhausted;out[5]=m.contention;}
 return rc;
}
int engine_force_node_limit(void *pointer,uint64_t now){
 struct drive_engine *e=pointer;
 struct lp_config limited=*e->config;limited.node_limit=1;
 const struct lp_config *original=e->config;e->config=&limited;
 int rc=engine_plan(e,now,4096);e->config=original;return rc;
}
/* 새 내부 차로 소비 경계의 actual C 시험 ABI다. 운영 wire나 임무 발급이 아니다. */
uint32_t probe_task_lane(void *pointer,float x,float y,float yaw,float error){
 struct probe *p=pointer;return tr_unique_lane(&p->snapshot.world,(dw_pose){x,y,yaw},error,&p->config);
}
int probe_task_contains(void *pointer,uint32_t from,uint32_t to,int change,float x,float y,float yaw,float error){
 struct probe *p=pointer;struct tr_intent task={true,change!=0,to,from,from,0};struct tr_view view;
 return tr_open(&p->snapshot.world,&task,&view)&&tr_contains(&view,(dw_pose){x,y,yaw},error,&p->config);
}
int probe_task_goal(void *pointer,uint32_t id,float x,float y,float horizon,float *out){
 struct probe *p=pointer;struct tr_intent task={true,false,id,id,id,0};dw_pose goal;
 enum lp_result rc=lp_task_goal(&p->snapshot.world,&task,(dw_pose){x,y,0},horizon,.4f,&goal);
 if(rc==LP_PATH_FOUND){out[0]=goal.x;out[1]=goal.y;out[2]=goal.yaw;}return rc;
}
int probe_basis_accept(void *pointer){struct probe *p=pointer;return tr_basis_accept(&p->basis,&p->snapshot.world);}
unsigned probe_basis_count(void *pointer){return ((struct probe *)pointer)->basis.count;}
int probe_basis_fault(void *pointer,unsigned variant){
 /* 형식 거절의 내부 값 주입이다. wire가 수락한 geometry라는 뜻이 아니다. */
 struct probe *p=pointer;dw_world *w=&p->snapshot.world;pb_size_t count=w->reference_count;
 dw_point last=w->reference[count-1];char calibration=w->calibration[0];
 if(variant==1)w->reference[count-1]=w->reference[0];else if(variant==2)w->reference_count=65;else w->calibration[0]='z';
 int accepted=tr_basis_accept(&p->basis,w);w->reference_count=count;w->reference[count-1]=last;w->calibration[0]=calibration;return accepted;
}
int probe_parking_approach(void *pointer,uint32_t lane,uint32_t slot){
 struct probe *p=pointer;struct tr_intent task={true,false,lane,lane,lane,0};struct tr_view view;
 return tr_open(&p->snapshot.world,&task,&view)&&tr_parking_approach(&view,slot);
}
int engine_task_state(void *pointer,uint32_t *out){
 struct drive_engine *e=pointer;struct de_reference h;const struct lp_path *p;
 if(de_path_acquire(e,&h,&p))return -1;
 out[0]=p->task.home_lane_id;out[1]=p->task.from_lane_id;out[2]=p->task.selected_lane_id;
 out[3]=p->task.changing;out[4]=p->basis.count;
 (void)de_path_release(e,h);return 0;
}

/* signed 알고리즘/추적기 시험 입구. 외부 Gate fact는 명시 software fixture다. */
int probe_signed_plan(void *pointer,float x,float y,float yaw,float gx,float gy,float gyaw,int first,int last,unsigned cusps){
 struct probe *p=pointer;uint64_t now;if(clock_us(NULL,&now))return LP_CLOCK;
 struct lp_budget budget={now+10000000,clock_us,cancelled,p,&p->collision_index};
 enum lp_result rc=lp_start_signed(&p->search,&p->snapshot.world,(dw_pose){x,y,yaw},0,(dw_pose){gx,gy,gyaw},
  first,last,cusps,&p->config,&lp_selected_assets,budget);
 while(rc==LP_SEARCHING)rc=lp_advance(&p->search,16,&p->path);
 lp_tracker_begin(&p->tracker,1);return rc;
}
unsigned probe_segments(void *pointer,int *out){
 struct lp_path *path=&((struct probe *)pointer)->path;
 for(unsigned i=0;i<path->execution.segments_count;i++){out[3*i]=path->execution.segments[i].first_index;out[3*i+1]=path->execution.segments[i].last_index;out[3*i+2]=path->execution.segments[i].signed_direction;}
 return path->execution.segments_count;
}
static dw_pose fixture_step(dw_pose p,float speed,float delta,float dt,float wheelbase){
 float turn=speed/wheelbase*tanf(delta)*dt,ds=speed*dt;
 if(fabsf(turn)<1e-10f){p.x+=ds*cosf(p.yaw);p.y+=ds*sinf(p.yaw);}
 else{float radius=ds/turn;p.x+=radius*(sinf(p.yaw+turn)-sinf(p.yaw));p.y-=radius*(cosf(p.yaw+turn)-cosf(p.yaw));}
 p.yaw=dw_angle(p.yaw+turn);return p;
}
void probe_signed_fixture(void *pointer,unsigned variant){
 struct probe *p=pointer;struct lp_path *path=&p->path;memset(path,0,sizeof(*path));
 path->world_features=p->snapshot.world.required_features;strcpy(path->frame,p->snapshot.world.frame_epoch);strcpy(path->odom,p->snapshot.world.odom_epoch);
 strcpy(path->configuration,p->snapshot.world.configuration);strcpy(path->reference,p->snapshot.world.reference_id);
 path->revision=19;path->maneuver_revision=23;path->execution.segments_count=variant>=2?2:1;
 int gear=variant==1||variant==3?-1:1;float delta=gear<0?.15f:0;
 path->execution.segments[0]=(xenova_drive_GearSegment){0,32,gear};
 dw_pose pose={2,1.6f,0};
 for(unsigned i=0;i<=32;i++){path->points[path->count++]=(xenova_drive_PathPoint){.pose=pose,.steering_rad=delta,.velocity_mps=(i==32?0:gear*.2f)};pose=fixture_step(pose,gear*.4f,delta,.05f,p->config.wheelbase);}
 if(variant>=2){pose=path->points[32].pose;gear=-gear;delta=gear<0?.15f:0;path->execution.segments[1]=(xenova_drive_GearSegment){33,65,gear};
  for(unsigned i=0;i<=32;i++){path->points[path->count++]=(xenova_drive_PathPoint){.pose=pose,.steering_rad=delta,.velocity_mps=(i==0||i==32?0:gear*.2f)};pose=fixture_step(pose,gear*.4f,delta,.05f,p->config.wheelbase);}}
 path->goal=path->merge_goal=path->points[path->count-1].pose;lp_tracker_begin(&p->tracker,1);
}
int probe_track_sample(void *pointer,unsigned index,uint64_t stamp,float velocity,float *out){
 struct probe *p=pointer;if(index>=p->path.count)return LP_INPUT;
 dw_vehicle vehicle={.quality=3,.pi_sample_us=stamp,.velocity_mps=velocity,.has_measured_steering_rad=true,
  .measured_steering_rad=p->path.points[index].steering_rad,.position_error_m=.003f,.heading_error_rad=.003f,
  .has_steering_error_rad=true,.steering_error_rad=.001f};
 struct lp_command command;struct lp_metrics metrics={0};uint64_t now;if(clock_us(NULL,&now))return LP_CLOCK;
 struct lp_budget budget={now+1000000,clock_us,NULL,NULL,&p->collision_index};
 enum lp_result rc=lp_track(&p->tracker,&p->path,&p->snapshot.world,p->path.points[index].pose,&vehicle,&p->config,.02f,&command,&metrics,&budget);
 out[0]=command.velocity;out[1]=command.steer;out[2]=command.gate_required;out[3]=command.segment_index;out[4]=command.requested_direction;out[5]=p->tracker.progress;
 return rc;
}
void probe_seed_cusp(void *pointer){struct probe *p=pointer;p->path.initial_tracker_index=p->path.execution.segments[0].last_index;lp_tracker_begin(&p->tracker,2);}
int probe_gate(void *pointer,unsigned variant){
 struct probe *p=pointer;struct lp_direction_fact fact={.path_revision=19,.maneuver_revision=23,.calculation_sequence=41,
  .serial_stop_sequence=9001,.gate_revision=7,.expires_pi_us=2000000,.next_segment=1,.direction=p->path.execution.segments[1].signed_direction,.current=true,.stationary=true,.steering_settled=true};
 if(variant==1)fact.calculation_sequence=9001;else if(variant==2)fact.serial_stop_sequence=0;else if(variant==3)fact.next_segment=2;
 else if(variant==4)fact.direction=-fact.direction;else if(variant==5)fact.steering_settled=false;else if(variant==6)fact.expires_pi_us=1000000;
 else if(variant==7)fact.maneuver_revision++;else if(variant==8)fact.current=false;
 return lp_tracker_next_segment(&p->tracker,&p->path,1000000,41,&fact);
}
