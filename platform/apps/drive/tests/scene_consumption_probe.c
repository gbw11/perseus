/* Actual decoder/pool/planner Scene consumption; explicit software time/source fixtures. */
#include "engine.h"
#include "pb_decode.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>

extern const struct lp_config lp_selected_config;
extern const struct lp_assets lp_selected_assets;
static uint8_t wire[DW_WIRE_MAX];
static xenova_drive_DriveLink link;
static xenova_drive_SceneGrant grant;
static xenova_drive_MissionGrant mission;
static dw_world input;
static struct dw_trust trust;
static size_t read_wire(const char *path){
 FILE *f=fopen(path,"rb");assert(f);size_t n=fread(wire,1,sizeof(wire),f);
 assert(!ferror(f)&&fgetc(f)==EOF);assert(!ferror(f)&&!fclose(f));return n;
}
static void read_world(const char *path){
 size_t n=read_wire(path);pb_istream_t stream=pb_istream_from_buffer(wire,n);
 assert(pb_decode(&stream,xenova_drive_WorldSnapshot_fields,&input));
}
static void read_grant(const char *path){
 size_t n=read_wire(path);assert(dw_link_decode(wire,n,&link)==DW_OK);
 assert(link.which_payload==xenova_drive_DriveLink_scene_grant_tag);grant=link.payload.scene_grant;
}
static void trust_fields(const xenova_drive_WorldTrust *t){
 memset(&trust,0,sizeof(trust));
 strcpy(trust.vehicle,t->vehicle);strcpy(trust.mapper_boot,t->mapper_boot);strcpy(trust.mapper_instance,t->mapper_instance);
 strcpy(trust.map_epoch,t->map_epoch);strcpy(trust.frame_epoch,t->frame_epoch);strcpy(trust.odom_epoch,t->odom_epoch);
 strcpy(trust.configuration,t->configuration);strcpy(trust.calibration,t->calibration);strcpy(trust.primitive,t->primitive);
 strcpy(trust.clock.epoch,t->clock_epoch);trust.clock.lower_offset_us=t->lower_offset_us;
 trust.clock.upper_offset_us=t->upper_offset_us;trust.clock.expires_pi_us=t->expires_pi_us;
 trust.clock.max_error_us=t->max_error_us;trust.max_live_age_us=300000;
}
static void fixture_trust(void){
 xenova_drive_WorldTrust t={0};
 strcpy(t.vehicle,input.vehicle);strcpy(t.mapper_boot,input.mapper_boot);strcpy(t.mapper_instance,input.mapper_instance);
 strcpy(t.map_epoch,input.map_epoch);strcpy(t.frame_epoch,input.frame_epoch);strcpy(t.odom_epoch,input.odom_epoch);
 strcpy(t.configuration,input.configuration);strcpy(t.calibration,input.calibration);strcpy(t.primitive,input.primitive);
 strcpy(t.clock_epoch,input.clock_epoch);t.lower_offset_us=100000;t.upper_offset_us=101000;
 t.expires_pi_us=3000000;t.max_error_us=2000;trust_fields(&t);
}
static struct dw_pool *pool_new(void){
 struct dw_pool *p=calloc(1,sizeof(*p));assert(p);dw_pool_init(p);
 assert(dw_pool_refresh(p,&trust)==DW_OK);return p;
}
static int clock_read(void *unused,uint64_t *out){(void)unused;*out=5000000;return 0;}
static struct lp_budget budget(void){return (struct lp_budget){6000000,clock_read,NULL,NULL,NULL};}
static dw_vehicle vehicle(unsigned seq,uint64_t at){
 dw_vehicle v={.major=1,.sequence=seq,.source_sample=seq,.source_us=at-100000,.pi_sample_us=at,
  .clock_error_us=500,.quality=3,.has_estimated_steering_rad=true,.has_steering_error_rad=true,
  .position_error_m=.005f,.heading_error_rad=.01f,.steering_error_rad=.001f};
 strcpy(v.vehicle,trust.vehicle);strcpy(v.host_boot,grant.pi_host_boot);strcpy(v.device_session,trust.mapper_boot);
 strcpy(v.odom_epoch,trust.odom_epoch);strcpy(v.calibration,trust.calibration);return v;
}
static struct drive_engine *engine_new(const char *world,bool scene){
 struct drive_engine *e=calloc(1,sizeof(*e));assert(e);de_init(e,&lp_selected_config,&lp_selected_assets);
 assert(dw_pool_refresh(&e->maps,&trust)==DW_OK);
 if(scene)assert(dw_pool_scene(&e->maps,&grant,1200000)==DW_OK);
 dw_vehicle v=vehicle(1,1200000);assert(de_vehicle(e,&v)==DE_OK);
 size_t n=read_wire(world);assert(de_world(e,wire,n,1200000)==DE_OK&&e->maps.current>=0);
 v=vehicle(2,1220000);assert(de_vehicle(e,&v)==DE_OK);return e;
}
static void plan_at(struct drive_engine *e,uint64_t now){
 assert(de_plan_start(e,now,budget())==DE_OK);
 for(unsigned i=0;i<4096&&e->planning;i++)(void)de_plan_slice(e,8,now);
 assert(!e->planning&&e->current>=0&&!e->path_invalidated);
}
static void plan(struct drive_engine *e){plan_at(e,1220000);}
static void export_state(struct drive_engine *e,dw_state *s){
 const struct lp_path *path=&e->paths[e->current].path;memset(s,0,sizeof(*s));
 lp_state_export(path,s);s->source_generation=1;s->state_revision=1;
 strcpy(s->source_vm,"77777777-7777-4777-8777-777777777777");
 strcpy(s->source_execution,"88888888-8888-4888-8888-888888888888");
 strcpy(s->source_app_instance,"99999999-9999-4999-8999-999999999999");
 strcpy(s->frame_epoch,path->frame);strcpy(s->odom_epoch,path->odom);strcpy(s->configuration,path->configuration);
 strcpy(s->primitive,trust.primitive);strcpy(s->calibration,trust.calibration);strcpy(s->reference_id,path->reference);
 strcpy(s->map_epoch,trust.map_epoch);strcpy(s->message_id,"88888888-8888-4888-8888-888888888888");
 s->reference_revision=input.reference_revision;s->path_revision=path->revision;s->maneuver_revision=path->maneuver_revision;
 s->direction=path->direction;s->maneuver_phase=path->phase;s->merge_goal=path->merge_goal;
 s->vehicle_sequence=2;s->calculation_sequence=1;s->complete=true;s->observation_seq=input.observation_seq;
 s->path_count=path->count;memcpy(s->path,path->points,path->count*sizeof(path->points[0]));
 s->has_world_features=true;s->world_features=input.required_features;
 assert(dw_state_validate(s)==DW_OK);
}
static void revoked(xenova_drive_SceneGrant *g){
 g->lifecycle_state=3;g->lifecycle_revision++;g->scene.admission_revision++;
 g->source_admission_revision++;g->request_nonce.bytes[0]^=1;
}
static void external(int argc,char **argv){
 assert(argc==9||argc==10);read_world(argv[2]);
 size_t n=read_wire(argv[5]);xenova_drive_WorldTrust original={0};
 pb_istream_t stream=pb_istream_from_buffer(wire,n);assert(pb_decode(&stream,xenova_drive_WorldTrust_fields,&original));
 trust_fields(&original);read_grant(argv[3]);uint64_t now=strtoull(argv[8],NULL,10);
 struct dw_snapshot *decoded=calloc(1,sizeof(*decoded));assert(decoded);n=read_wire(argv[2]);
 assert(dw_decode(wire,n,&trust,now,decoded)==DW_OK);input=decoded->world;free(decoded);
 assert(!strcmp(grant.pi_host_boot,argv[6])&&!strcmp(grant.gateway_instance,argv[7]));
 struct dw_pool *p=pool_new();uint64_t generation=0,end=0;
 assert(dw_scene_time(p,&input,now,NULL,NULL)==DW_SOURCE);
 assert(dw_pool_scene(p,&grant,now)==DW_OK);
 assert(dw_scene_time(p,&input,now,&end,&generation)==DW_OK&&generation);
 uint64_t original_end=grant.valid_until_pi_us;
 assert(end==original_end&&dw_scene_generation(p,generation,original_end,NULL)==DW_SOURCE);
 struct drive_engine *e=NULL;struct lp_command command;dw_vehicle current;
 if(argc==10){
  n=read_wire(argv[9]);assert(dw_link_decode(wire,n,&link)==DW_OK&&link.which_payload==xenova_drive_DriveLink_mission_grant_tag);
  mission=link.payload.mission_grant;
  e=calloc(1,sizeof(*e));assert(e);de_init(e,&lp_selected_config,&lp_selected_assets);
  assert(dw_pool_refresh(&e->maps,&trust)==DW_OK&&dw_pool_scene(&e->maps,&grant,now)==DW_OK);
  assert(now>1000);dw_vehicle v=vehicle(1,now-1000);assert(de_vehicle(e,&v)==DE_OK);
  n=read_wire(argv[2]);assert(de_world(e,wire,n,now)==DE_OK&&e->maps.current>=0);
  v=vehicle(2,now);assert(de_vehicle(e,&v)==DE_OK);
  assert(de_mission(e,&mission,now)==DE_OK);plan_at(e,now);
  assert(de_track_mission(e,&mission,now,.02f,&command,&current,budget())==DE_OK&&command.velocity>0);
  assert(e->paths[e->current].scene_generation&&command.valid_until_pi_us<=original_end);
 }
 read_grant(argv[4]);assert(!strcmp(grant.pi_host_boot,argv[6])&&!strcmp(grant.gateway_instance,argv[7]));
 assert(grant.lifecycle_state==3&&dw_pool_scene(p,&grant,now)==DW_OK);
 assert(dw_scene_generation(p,generation,now,NULL)==DW_SOURCE);
 assert(dw_scene_time(p,&input,now,NULL,NULL)==DW_SOURCE);free(p);
 if(e){
  int held=e->current;assert(dw_pool_scene(&e->maps,&grant,now)==DW_OK);
  assert(de_track_mission(e,&mission,now,.02f,&command,&current,budget())!=DE_OK);
  assert(e->current==held&&command.velocity==0&&command.emergency);free(e);
  puts("external original actor grant/actual engine held plan fenced OK");
 }
 puts("external scene decoder/current grant/revoke/expiry OK");
}
int main(int argc,char **argv){
 assert(argc>=5);
 if(!strcmp(argv[1],"external")){external(argc,argv);return 0;}
 const char *test=argv[1];read_world(argv[2]);read_grant(argv[3]);fixture_trust();
 if(!strcmp(test,"legacy")){
  size_t n=read_wire(argv[2]);struct dw_snapshot *s=calloc(1,sizeof(*s));assert(s);
  assert(dw_decode(wire,n,&trust,1200000,s)==DW_VERSION);free(s);puts("legacy retained rejected");return 0;
 }
 size_t n=read_wire(argv[4]);assert(dw_link_decode(wire,n,&link)==DW_OK);
 assert(link.which_payload==xenova_drive_DriveLink_mission_grant_tag);mission=link.payload.mission_grant;
 if(!strcmp(test,"restore")){grant.scene.allow_restore_after_mapper_restart=true;input.scene.allow_restore_after_mapper_restart=true;}
 struct dw_pool *p=pool_new();uint64_t gen=0,end=0;
 bool live=!strcmp(test,"live-isolation");
 if(!live){assert(dw_scene_time(p,&input,1220000,NULL,NULL)==DW_SOURCE);
  assert(dw_pool_scene(p,&grant,1200000)==DW_OK);
  assert(dw_scene_time(p,&input,1220000,&end,&gen)==DW_OK&&gen==1&&end==grant.valid_until_pi_us);}
 if(!strcmp(test,"gate")){
  assert(dw_scene_generation(p,gen,grant.valid_until_pi_us-1,NULL)==DW_OK);
  assert(dw_scene_generation(p,gen,grant.valid_until_pi_us,NULL)==DW_SOURCE);
 }else if(!strcmp(test,"duplicate")){
  uint64_t original_end=grant.valid_until_pi_us;
  assert(dw_pool_scene(p,&grant,1220000)==DW_STALE&&p->scene_generation==gen&&p->scene.valid_until_pi_us==original_end);
  dw_scene_fence(p);assert(dw_pool_scene(p,&grant,1220000)==DW_STALE);
  assert(dw_scene_time(p,&input,1220000,NULL,NULL)==DW_SOURCE);
 }else if(!strcmp(test,"old")){
  grant.lifecycle_revision--;assert(dw_pool_scene(p,&grant,1220000)==DW_STALE);
  assert(p->scene_generation==gen&&dw_scene_time(p,&input,1220000,NULL,NULL)==DW_OK);
 }else if(!strcmp(test,"conflict")){
  grant.request_nonce.bytes[0]^=1;assert(dw_pool_scene(p,&grant,1220000)==DW_SOURCE);
  assert(p->scene_generation==gen&&dw_scene_time(p,&input,1220000,NULL,NULL)==DW_OK);
 }else if(!strcmp(test,"no-renew")){
  grant.lifecycle_revision++;grant.valid_until_pi_us++;
  assert(dw_pool_scene(p,&grant,1220000)==DW_SOURCE&&p->scene_generation==gen);
 }else if(!strcmp(test,"source-hwm")){
  grant.source_admission_revision--;assert(dw_pool_scene(p,&grant,1220000)==DW_STALE&&p->scene_generation==gen);
 }else if(!strcmp(test,"full-ref")){
  input.scene.coverage_profile_sha256.bytes[0]^=1;
  assert(dw_scene_time(p,&input,1220000,NULL,NULL)==DW_SOURCE&&p->scene_generation==gen);
 }else if(!strcmp(test,"policy-ref")){
  input.scene.allow_restore_after_mapper_restart=!input.scene.allow_restore_after_mapper_restart;
  assert(dw_scene_time(p,&input,1220000,NULL,NULL)==DW_SOURCE);
 }else if(!strcmp(test,"source-change")){
  strcpy(trust.mapper_boot,"88888888-8888-4888-8888-888888888888");
  assert(dw_pool_refresh(p,&trust)==DW_OK&&dw_scene_generation(p,gen,1220000,NULL)==DW_SOURCE);
  assert(dw_pool_scene(p,&grant,1220000)==DW_SOURCE);
 }else if(!strcmp(test,"renew")){
  uint32_t source=p->source_revision;trust.clock.expires_pi_us++;trust.clock.lower_offset_us++;
  assert(dw_pool_refresh(p,&trust)==DW_OK&&p->source_revision==source);
  assert(p->scene_generation==gen&&dw_scene_time(p,&input,1220000,NULL,NULL)==DW_OK);
 }else if(!strcmp(test,"malformed")){
  grant.request_nonce.size=15;assert(dw_pool_scene(p,&grant,1220000)==DW_ARRAY);
  assert(p->scene_generation==gen&&dw_scene_time(p,&input,1220000,NULL,NULL)==DW_OK);
 }else if(!strcmp(test,"revoke")){
  xenova_drive_SceneGrant old=grant;revoked(&grant);assert(dw_pool_scene(p,&grant,1220000)==DW_OK);
  assert(dw_scene_generation(p,gen,1220000,NULL)==DW_SOURCE);
  assert(dw_pool_scene(p,&old,1220000)==DW_STALE);
  grant.lifecycle_state=2;grant.lifecycle_revision++;
  assert(dw_pool_scene(p,&grant,1220000)==DW_SOURCE);
 }else if(!strcmp(test,"restore")){
  uint64_t original_end=grant.valid_until_pi_us;
  grant.lifecycle_state=3;grant.lifecycle_revision++;
  assert(dw_pool_scene(p,&grant,1220000)==DW_OK);
  grant.lifecycle_state=2;grant.lifecycle_revision++;
  assert(dw_pool_scene(p,&grant,1220000)==DW_SOURCE);
  strcpy(trust.mapper_instance,"99999999-9999-4999-8999-999999999999");
  assert(dw_pool_refresh(p,&trust)==DW_OK&&dw_scene_time(p,&input,1220000,NULL,NULL)==DW_SOURCE);
  grant.purpose=1;grant.has_restore_checkpoint_sha256=true;grant.restore_checkpoint_sha256.size=32;
  memset(grant.restore_checkpoint_sha256.bytes,10,32);
  grant.request_nonce.bytes[0]^=1;grant.source_admission_revision++;grant.lifecycle_revision++;grant.lifecycle_state=1;
  assert(dw_pool_scene(p,&grant,1220000)==DW_OK&&dw_scene_time(p,&input,1220000,NULL,NULL)==DW_SOURCE);
  grant.lifecycle_state=2;grant.lifecycle_revision++;
  assert(dw_pool_scene(p,&grant,1220000)==DW_OK&&grant.valid_until_pi_us==original_end);
  assert(dw_scene_time(p,&input,1220000,NULL,NULL)==DW_SOURCE);
  strcpy(input.mapper_instance,trust.mapper_instance);
  assert(dw_scene_time(p,&input,1220000,NULL,NULL)==DW_OK);
  assert(dw_scene_generation(p,gen,1220000,NULL)==DW_SOURCE);
 }else if(!strcmp(test,"new-scene")){
  grant.scene.admission_id.bytes[0]^=1;grant.issued_pi_us++;grant.valid_until_pi_us++;
  grant.request_nonce.bytes[0]^=1;
  assert(dw_pool_scene(p,&grant,1220000)==DW_STALE&&p->scene_generation==gen);
  grant.source_admission_revision++;
  assert(dw_pool_scene(p,&grant,1220000)==DW_OK&&p->scene_generation>gen);
  assert(dw_scene_time(p,&input,1220000,NULL,NULL)==DW_SOURCE);
  input.scene=grant.scene;assert(dw_scene_time(p,&input,1220000,NULL,NULL)==DW_OK);
 }else if(!strcmp(test,"unadmitted")){
  struct dw_pool *q=pool_new();grant.lifecycle_state=0;
  grant.has_frozen_evidence_sha256=false;grant.frozen_survey_revision=grant.frozen_hazard_revision=0;
  assert(dw_pool_scene(q,&grant,1220000)==DW_OK&&dw_scene_time(q,&input,1220000,NULL,NULL)==DW_SOURCE);free(q);
 }else if(!strcmp(test,"overflow")){
  p->scene_generation=UINT64_MAX;grant.lifecycle_revision++;
  assert(dw_pool_scene(p,&grant,1220000)==DW_NO_SPACE&&atomic_load(&p->scene_fenced));
 }else if(!strcmp(test,"engine-pending-revoke")){
  struct drive_engine *e=calloc(1,sizeof(*e));assert(e);de_init(e,&lp_selected_config,&lp_selected_assets);
  assert(dw_pool_refresh(&e->maps,&trust)==DW_OK&&dw_pool_scene(&e->maps,&grant,1200000)==DW_OK);
  n=read_wire(argv[2]);assert(de_world(e,wire,n,1200000)==DE_OK&&e->receive_awaiting&&e->maps.current<0);
  revoked(&grant);assert(dw_pool_scene(&e->maps,&grant,1220000)==DW_OK);
  assert(de_receive_poll(e,1220000)==DE_CHANGED&&!e->receive_awaiting&&e->maps.current<0);free(e);
 }else if(!strncmp(test,"engine-",7)||live){
  struct drive_engine *e=engine_new(argv[2],!live);
  if(!strcmp(test,"engine-no-mission")){assert(de_plan_start(e,1220000,budget())==DE_STATE);}
  else if(!strcmp(test,"engine-wrong-mission")){
   mission.scene_revision++;assert(de_mission(e,&mission,1220000)==DE_OK);
   assert(de_plan_start(e,1220000,budget())==DE_STATE);
  }else if(!strcmp(test,"engine-mission-null")){
   assert(de_mission(e,&mission,1220000)==DE_OK);
   assert(de_plan_start(e,1220000,budget())==DE_OK&&e->planning);
   uint32_t requested=e->requested;assert(de_mission(e,NULL,1220000)==DE_OK&&e->requested==requested+1);
   assert(de_mission(e,NULL,1220000)==DE_OK&&e->requested==requested+1);
   assert(de_plan_slice(e,8,1220000)==DE_CANCELLED&&e->current<0&&!e->stopping);
  }else if(!strcmp(test,"engine-cancel")){
   assert(de_mission(e,&mission,1220000)==DE_OK);
   assert(de_plan_start(e,1220000,budget())==DE_OK&&e->planning);
   revoked(&grant);assert(dw_pool_scene(&e->maps,&grant,1220000)==DW_OK);
   assert(de_plan_slice(e,8,1220000)==DE_CANCELLED&&e->current<0&&!e->stopping);
  }else{
   if(!live)assert(de_mission(e,&mission,1220000)==DE_OK);
   plan(e);
   if(!strcmp(test,"engine-apply")||!strcmp(test,"engine-apply-expiry")){
    dw_state *state=calloc(1,sizeof(*state));assert(state);export_state(e,state);
    struct drive_engine *target=engine_new(argv[2],true);
    assert(de_mission(target,&mission,1220000)==DE_OK);
    assert(de_apply(target,state,1220000,budget())==DE_BUSY&&target->apply_pending&&target->current<0);
    if(!strcmp(test,"engine-apply-expiry")){
     assert(de_apply_finish(target,grant.valid_until_pi_us)==DE_STATE&&!target->apply_pending&&target->current<0);
    }else assert(de_apply_finish(target,1220001)==DE_OK&&!target->apply_pending&&target->current>=0);
    free(state);free(target);free(e);free(p);puts("original Scene apply final-time gate OK");return 0;
   }
   struct lp_command command;dw_vehicle current;
   enum de_result result=live?de_track(e,1220000,.02f,&command,&current,budget()):
    de_track_mission(e,&mission,1220000,.02f,&command,&current,budget());
   assert(result==DE_OK&&command.velocity>0);
   if(live){dw_scene_fence(&e->maps);assert(de_track(e,1220000,.02f,&command,&current,budget())==DE_OK);}
   else{
    assert(command.valid_until_pi_us<=grant.valid_until_pi_us);
    const struct lp_path *path=&e->paths[e->current].path;
    assert(path->execution.mission.scene_admission_revision==grant.scene.admission_revision);
    assert(!memcmp(path->execution.mission.scene_admission_id.bytes,grant.scene.admission_id.bytes,16));
    if(!strcmp(test,"engine-expiry")){
     assert(de_track_mission(e,&mission,grant.valid_until_pi_us,.02f,&command,&current,budget())!=DE_OK);
    }else{
     revoked(&grant);assert(dw_pool_scene(&e->maps,&grant,1220000)==DW_OK);
     assert(de_track_mission(e,&mission,1220000,.02f,&command,&current,budget())!=DE_OK);
    }
    assert(command.velocity==0&&command.emergency);
   }
  }
  free(e);
 }else assert(!"unknown scene test");
 free(p);puts("scene current authority/engine boundary OK");return 0;
}
