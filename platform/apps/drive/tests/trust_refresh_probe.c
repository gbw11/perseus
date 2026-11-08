/* SPDX-License-Identifier: Apache-2.0 */
/* 합성 시각/입력으로 실제 pool과 planner를 검사한다. OS clock admission 증명은 아니다. */
#include "engine.h"
#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern const struct lp_config lp_selected_config;
extern const struct lp_assets lp_selected_assets;
static const struct dw_trust original={.vehicle="rc-test-1",.mapper_boot="11111111-1111-4111-8111-111111111111",
 .mapper_instance="22222222-2222-4222-8222-222222222222",.map_epoch="33333333-3333-4333-8333-333333333333",
 .frame_epoch="44444444-4444-4444-8444-444444444444",.odom_epoch="55555555-5555-4555-8555-555555555555",
 .configuration="lattice-lab-1",.calibration="encoder-lab-1",.primitive=TEST_PRIMITIVE_SHA,
 .clock={.epoch="66666666-6666-4666-8666-666666666666",.lower_offset_us=100000,
 .upper_offset_us=101000,.expires_pi_us=1300000,.max_error_us=2000},.max_live_age_us=300000};
static struct dw_trust renewed(void){
 struct dw_trust result=original;
 result.clock.lower_offset_us=100200;result.clock.upper_offset_us=100800;result.clock.expires_pi_us=4000000;
 return result;
}
static void retained_time(const struct dw_snapshot *value){
 uint64_t deadline=0;
 assert(value->received_us==1200000&&value->valid_until_us==1300000);
 assert(dw_snapshot_time(value,1220000,300000,&deadline)==DW_OK&&deadline==1300000);
 assert(dw_snapshot_time(value,1300001,300000,NULL)==DW_TIME);
}
static void pool_test(const uint8_t *wire,size_t length){
 struct dw_pool *pool=calloc(1,sizeof(*pool));assert(pool);
 struct dw_snapshot sample,*next;const struct dw_snapshot *held;struct dw_handle w,r;
 assert(dw_decode(wire,length,&original,1200000,&sample)==DW_OK);
 dw_pool_init(pool);assert(dw_pool_refresh(pool,&original)==DW_OK);assert(pool->source_revision==1);
 assert(dw_reserve(pool,&w,&next)==DW_OK);*next=sample;assert(dw_publish(pool,w)==DW_OK);
 assert(dw_acquire(pool,&r,&held)==DW_OK);uint64_t age=held->observed_upper_age_us;
 assert(dw_reserve(pool,&w,&next)==DW_OK);*next=sample;next->world.sequence++;
 struct dw_trust update=renewed();assert(dw_pool_refresh(pool,&update)==DW_OK);
 assert(pool->source_revision==1&&dw_source_valid(pool,r)==DW_OK);
 assert(held->observed_upper_age_us==age);retained_time(held);
 /* 이미 예약한 writer도 같은 source 갱신 때문에 잃지 않는다. */
 assert(dw_publish(pool,w)==DW_OK);assert(dw_source_valid(pool,r)==DW_OK);retained_time(held);
 struct dw_snapshot later;
 assert(dw_decode(wire,length,&pool->trust,1200000,&later)==DW_OK);
 assert(later.valid_until_us==4000000&&later.observed_upper_age_us+200==age);
 assert(dw_release(pool,r)==DW_OK);
 /* revision 포화에서도 같은 source time 갱신은 새 revision을 소비하지 않는다. */
 pool->source_revision=UINT32_MAX;update.clock.expires_pi_us++;
 assert(dw_pool_refresh(pool,&update)==DW_OK&&pool->source_revision==UINT32_MAX);
 update.max_live_age_us++;
 assert(dw_pool_refresh(pool,&update)==DW_NO_SPACE&&pool->trust.max_live_age_us==300000);
 free(pool);
}
static void identity_test(const uint8_t *wire,size_t length){
 struct dw_pool *pool=calloc(1,sizeof(*pool));assert(pool);
 struct dw_snapshot sample,*next;const struct dw_snapshot *held;struct dw_handle w,r;
 assert(dw_decode(wire,length,&original,1200000,&sample)==DW_OK);
 /* 각 의미 항목 하나만 바뀌어도 held reader와 미완 writer 모두 fence한다. */
 for(unsigned field=0;field<12;field++){
  dw_pool_init(pool);assert(dw_pool_refresh(pool,&original)==DW_OK);
  assert(dw_reserve(pool,&w,&next)==DW_OK);*next=sample;assert(dw_publish(pool,w)==DW_OK);
  assert(dw_acquire(pool,&r,&held)==DW_OK);
  assert(dw_reserve(pool,&w,&next)==DW_OK);*next=sample;next->world.sequence++;
  struct dw_trust update=original;
  char *ids[]={update.vehicle,update.mapper_boot,update.mapper_instance,update.map_epoch,update.frame_epoch,
   update.odom_epoch,update.configuration,update.calibration,update.primitive,update.clock.epoch};
  if(field<10)ids[field][0]=ids[field][0]=='a'?'b':'a';
  else if(field==10)update.max_live_age_us++;else update.clock.max_error_us++;
  assert(dw_pool_refresh(pool,&update)==DW_OK&&pool->source_revision==2);
  assert(dw_source_valid(pool,r)==DW_SOURCE&&dw_publish(pool,w)==DW_SOURCE);
  assert(dw_abort(pool,w)==DW_OK&&dw_release(pool,r)==DW_OK);
 }
 dw_pool_init(pool);assert(dw_pool_refresh(pool,&original)==DW_OK);
 struct dw_trust tail;memset(&tail,0xa5,sizeof(tail));
 strcpy(tail.vehicle,original.vehicle);strcpy(tail.mapper_boot,original.mapper_boot);
 strcpy(tail.mapper_instance,original.mapper_instance);strcpy(tail.map_epoch,original.map_epoch);
 strcpy(tail.frame_epoch,original.frame_epoch);strcpy(tail.odom_epoch,original.odom_epoch);
 strcpy(tail.configuration,original.configuration);strcpy(tail.calibration,original.calibration);
 strcpy(tail.primitive,original.primitive);strcpy(tail.clock.epoch,original.clock.epoch);
 tail.clock.lower_offset_us=original.clock.lower_offset_us;tail.clock.upper_offset_us=original.clock.upper_offset_us;
 tail.clock.expires_pi_us=original.clock.expires_pi_us;tail.clock.max_error_us=original.clock.max_error_us;
 tail.max_live_age_us=original.max_live_age_us;
 assert(dw_pool_refresh(pool,&tail)==DW_OK&&pool->source_revision==1);
 /* 명시적인 force API는 같은 의미의 trust여도 과거와 같이 fence한다. */
 assert(dw_pool_source(pool,&original)==DW_OK&&pool->source_revision==2);
 free(pool);
}
static void invalid_test(void){
 struct dw_pool *pool=calloc(1,sizeof(*pool));assert(pool);dw_pool_init(pool);
 assert(dw_pool_refresh(pool,&original)==DW_OK);
 for(unsigned variant=0;variant<10;variant++){
  struct dw_trust update=original;enum dw_result expected=DW_TIME;
  switch(variant){
   case 0:update.clock.lower_offset_us=101001;break;
   case 1:update.clock.upper_offset_us=102001;break;
   case 2:update.clock.lower_offset_us=INT64_MIN;update.clock.upper_offset_us=INT64_MAX;break;
   case 3:update.clock.expires_pi_us=0;break;
   case 4:update.clock.expires_pi_us=(uint64_t)INT64_MAX+1;break;
   case 5:update.max_live_age_us=0;expected=DW_CONFIGURATION;break;
   case 6:update.max_live_age_us=(uint64_t)INT64_MAX+1;expected=DW_CONFIGURATION;break;
   case 7:memset(update.clock.epoch,'x',sizeof(update.clock.epoch));expected=DW_SOURCE;break;
   case 8:update.mapper_boot[0]=0;expected=DW_SOURCE;break;
   default:memset(update.vehicle,'x',sizeof(update.vehicle));expected=DW_SOURCE;break;
  }
  assert(dw_pool_refresh(pool,&update)==expected&&pool->source_revision==1);
  assert(pool->trust.clock.expires_pi_us==original.clock.expires_pi_us);
  assert(pool->trust.clock.lower_offset_us==original.clock.lower_offset_us&&pool->trust.max_live_age_us==300000);
 }
 assert(dw_pool_refresh(pool,NULL)==DW_SOURCE&&pool->source_revision==1);
 assert(dw_pool_refresh(NULL,&original)==DW_SOURCE);
 atomic_flag_test_and_set(&pool->lock);
 struct dw_trust update=renewed();assert(dw_pool_refresh(pool,&update)==DW_BUSY&&pool->source_revision==1);
 atomic_flag_clear(&pool->lock);
 /* 폭 계산은 음수/양수 경계를 넘더라도 signed overflow 없이 검사한다. */
 update=original;update.clock.lower_offset_us=-1000;update.clock.upper_offset_us=1000;
 assert(dw_pool_refresh(pool,&update)==DW_OK&&pool->source_revision==1);
 update.clock.lower_offset_us=INT64_MIN;update.clock.upper_offset_us=INT64_MIN+2000;
 assert(dw_pool_refresh(pool,&update)==DW_OK&&pool->source_revision==1);
 free(pool);
}
static int clock_read(void *context,uint64_t *out){(void)context;*out=5000000;return 0;}
static dw_vehicle vehicle(unsigned sequence,uint64_t at){
 dw_vehicle value={.major=1,.sequence=sequence,.source_sample=sequence,.source_us=at-100000,
  .pi_sample_us=at,.clock_error_us=500,.quality=3,.has_estimated_steering_rad=true,
  .position_error_m=.005f,.heading_error_rad=.01f,.has_steering_error_rad=true,.steering_error_rad=.001f};
 strcpy(value.vehicle,original.vehicle);strcpy(value.host_boot,original.mapper_boot);
 strcpy(value.device_session,original.mapper_instance);strcpy(value.odom_epoch,original.odom_epoch);
 strcpy(value.calibration,original.calibration);return value;
}
static void engine_test(const uint8_t *wire,size_t length,bool change){
 struct drive_engine *e=calloc(1,sizeof(*e));assert(e);de_init(e,&lp_selected_config,&lp_selected_assets);
 assert(dw_pool_refresh(&e->maps,&original)==DW_OK);
 dw_vehicle first=vehicle(1,1200000),second=vehicle(2,1220000);
 /* 원 map 수신 시각보다 미래인 pose를 먼저 최신 표본으로 넣지 않는다. */
 assert(de_vehicle(e,&first)==DE_OK);
 assert(de_world(e,wire,length,1200000)==DE_OK&&e->maps.current>=0);
 assert(de_vehicle(e,&second)==DE_OK);
 struct lp_budget budget={6000000,clock_read,NULL,NULL,NULL};
 assert(de_plan_start(e,1220000,budget)==DE_OK&&e->planning);
 const struct dw_snapshot *held=e->planning_snapshot;retained_time(held);
 struct dw_handle handle=e->planning_map;struct dw_trust update=renewed();
 if(change)strcpy(update.clock.epoch,"77777777-7777-4777-8777-777777777777");
 assert(dw_pool_refresh(&e->maps,&update)==DW_OK);
 assert(e->planning&&e->planning_snapshot==held);
 assert(dw_source_valid(&e->maps,handle)==(change?DW_SOURCE:DW_OK));retained_time(held);
 enum de_result result=DE_WAITING;
 for(unsigned k=0;k<4096&&e->planning;k++)result=de_plan_slice(e,8,1220000);
 assert(!e->planning);
 if(change){assert(result!=DE_OK&&e->current<0);}
 else{
  assert(result==DE_OK&&e->current>=0&&e->discarded==0);
  struct lp_command command;dw_vehicle current;
  assert(de_track(e,1220000,.02f,&command,&current,budget)==DE_OK&&command.velocity>0);
  assert(command.valid_until_pi_us==1300000);
  assert(de_track(e,1300001,.02f,&command,&current,budget)==DE_EXPIRED&&command.velocity==0);
 }
 free(e);
}
int main(int argc,char **argv){
 assert(argc==3);uint8_t wire[DW_WIRE_MAX+1];FILE *file=fopen(argv[2],"rb");assert(file);
 size_t length=fread(wire,1,sizeof(wire),file);assert(!ferror(file)&&feof(file));assert(fclose(file)==0);
 assert(length&&length<=DW_WIRE_MAX);
 if(!strcmp(argv[1],"pool"))pool_test(wire,length);
 else if(!strcmp(argv[1],"identity"))identity_test(wire,length);
 else if(!strcmp(argv[1],"invalid"))invalid_test();
 else if(!strcmp(argv[1],"engine-renew"))engine_test(wire,length,false);
 else if(!strcmp(argv[1],"engine-change"))engine_test(wire,length,true);
 else assert(0);
 printf("{\"case\":\"%s\",\"result\":\"OK\"}\n",argv[1]);return 0;
}
