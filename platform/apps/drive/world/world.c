/* SPDX-License-Identifier: Apache-2.0 */
#include "world.h"
#include "pb_decode.h"
#include "pb_common.h"
#include <math.h>
#include <string.h>
#include <limits.h>

bool dw_pose_finite(dw_pose p) { return isfinite(p.x)&&isfinite(p.y)&&isfinite(p.yaw); }
float dw_angle(float a) { return remainderf(a,6.28318530718f); }
dw_pose dw_compose(dw_pose a,dw_pose b) {
 float c=cosf(a.yaw),s=sinf(a.yaw);
 return (dw_pose){a.x+c*b.x-s*b.y,a.y+s*b.x+c*b.y,dw_angle(a.yaw+b.yaw)};
}
dw_pose dw_inverse(dw_pose p) {
 float c=cosf(p.yaw),s=sinf(p.yaw);
 return (dw_pose){-c*p.x-s*p.y,s*p.x-c*p.y,-p.yaw};
}
static bool finite_point(dw_point p) { return isfinite(p.x)&&isfinite(p.y)&&fabsf(p.x)<1e6f&&fabsf(p.y)<1e6f; }
static bool id(const char *p) { return p&&*p&&strlen(p)<=64; }
static float cross(dw_point a,dw_point b,dw_point c){return (b.x-a.x)*(c.y-a.y)-(b.y-a.y)*(c.x-a.x);}
static bool between(float a,float b,float c){return c>=fminf(a,b)-1e-6f&&c<=fmaxf(a,b)+1e-6f;}
static bool intersects(dw_point a,dw_point b,dw_point c,dw_point d){
 float x=cross(a,b,c),y=cross(a,b,d),z=cross(c,d,a),w=cross(c,d,b);
 if(((x>0&&y<0)||(x<0&&y>0))&&((z>0&&w<0)||(z<0&&w>0)))return true;
 return (fabsf(x)<1e-6f&&between(a.x,b.x,c.x)&&between(a.y,b.y,c.y))||
        (fabsf(y)<1e-6f&&between(a.x,b.x,d.x)&&between(a.y,b.y,d.y))||
        (fabsf(z)<1e-6f&&between(c.x,d.x,a.x)&&between(c.y,d.y,a.y))||
        (fabsf(w)<1e-6f&&between(c.x,d.x,b.x)&&between(c.y,d.y,b.y));
}
static bool nonnegative(float value){return isfinite(value)&&value>=0;}
static bool positive63(uint64_t value){return value&&value<=INT64_MAX;}
static bool features(uint32_t value){return value==0||value==1||value==11||value==15;}
static bool nil_uuid(const uint8_t *p){
 for(unsigned i=0;i<16;i++)if(p[i])return false;
 return true;
}
static bool scene_pair(const uint8_t *uuid,uint64_t revision){return nil_uuid(uuid)==(revision==0);}
static bool simple(const dw_point *points,size_t count,bool closed){
 size_t edges=closed?count:count-1;
 for(size_t i=0;i<edges;i++){
  dw_point a=points[i],b=points[(i+1)%count];
  if(!finite_point(a)||!finite_point(b)||hypotf(a.x-b.x,a.y-b.y)<1e-6f)return false;
  for(size_t j=i+2;j<edges;j++){
   if(closed&&i==0&&j==edges-1)continue;
   if(intersects(a,b,points[j],points[(j+1)%count]))return false;
  }
 }
 return true;
}
static bool open_basis(const dw_point *points,size_t count){
 if(!simple(points,count,false)||hypotf(points[0].x-points[count-1].x,points[0].y-points[count-1].y)<1e-6f)return false;
 for(size_t i=1;i+1<count;i++){
  dw_point a=points[i-1],b=points[i],c=points[i+1];
  /* 인접 edge는 단순성 검사에서 공유 endpoint를 허용하지만 구간 되돌림은 station을 중복시킨다. */
  if(fabsf(cross(a,b,c))<1e-6f&&((b.x-a.x)*(c.x-b.x)+(b.y-a.y)*(c.y-b.y))<0)return false;
 }
 return true;
}
static bool convex4(const dw_point *points){
 if(!simple(points,4,true))return false;
 bool positive=true,negative=true;
 for(size_t i=0;i<4;i++){
  float turn=cross(points[i],points[(i+1)%4],points[(i+2)%4]);
  positive&=turn>1e-6f;negative&=turn< -1e-6f;
 }
 return positive||negative;
}
static enum dw_result lane_validate(const xenova_drive_LaneModel *model){
 size_t n=model->divider_count;
 if(model->lanes_count!=2||n<2||n>32||model->crossing.size!=n-1||model->divider_state.size!=n)return DW_ARRAY;
 if(!positive63(model->revision)||!nonnegative(model->divider_position_bound_m)||!nonnegative(model->divider_heading_bound_rad))return DW_NUMERIC;
 for(size_t i=0;i<n;i++)if(model->divider_state.bytes[i]>1||(i+1<n&&model->crossing.bytes[i]>1))return DW_ARRAY;
 if(!simple(model->divider,n,false))return DW_GEOMETRY;
 if(!model->lanes[0].lane_id||!model->lanes[1].lane_id||model->lanes[0].lane_id==model->lanes[1].lane_id||
    model->lanes[0].adjacent_lane_id!=model->lanes[1].lane_id||model->lanes[1].adjacent_lane_id!=model->lanes[0].lane_id)return DW_SOURCE;
 int signs[2]={0};
 for(size_t k=0;k<2;k++){
  const xenova_drive_LaneGeometry *lane=&model->lanes[k];
  if(lane->travel_direction!=1||lane->centerline_count!=n||lane->point_state.size!=n)return DW_ARRAY;
  if(!positive63(lane->revision)||!nonnegative(lane->position_bound_m)||!nonnegative(lane->heading_bound_rad))return DW_NUMERIC;
  dw_point outer[32],polygon[64];
  for(size_t i=0;i<n;i++){
   if(lane->point_state.bytes[i]>1||!finite_point(lane->centerline[i]))return DW_ARRAY;
   outer[i]=(dw_point){2*lane->centerline[i].x-model->divider[i].x,2*lane->centerline[i].y-model->divider[i].y};
   polygon[i]=model->divider[i];polygon[2*n-1-i]=outer[i];
   size_t index=i==n-1?n-2:i;
   float side=cross(model->divider[index],model->divider[index+1],lane->centerline[i]);
   int sign=side>1e-6f?1:side< -1e-6f?-1:0;
   if(!sign||(signs[k]&&signs[k]!=sign))return DW_GEOMETRY;
   signs[k]=sign;
   if(i){
    dw_point p=lane->centerline[i],previous=lane->centerline[i-1];
    dw_point d=model->divider[i],last=model->divider[i-1];
    if((p.x-previous.x)*(d.x-last.x)+(p.y-previous.y)*(d.y-last.y)<=0)return DW_GEOMETRY;
    float error=2*lane->position_bound_m+model->divider_position_bound_m;
    if(hypotf(outer[i].x-outer[i-1].x,outer[i].y-outer[i-1].y)<=2*error)return DW_GEOMETRY;
   }
  }
  if(!simple(lane->centerline,n,false)||!simple(polygon,2*n,true))return DW_GEOMETRY;
 }
 return signs[0]!=signs[1]?DW_OK:DW_GEOMETRY;
}
static enum dw_result slot_validate(const xenova_drive_ParkingSlot *slot){
 if(!slot->slot_id||!positive63(slot->geometry_revision)||!positive63(slot->occupancy_revision))return DW_SOURCE;
 if(slot->corners_count>4||slot->geometry_state>2||slot->occupancy_state>2||slot->occupancy_reason>7||
    (slot->source_kinds&~7u)||slot->entry_edge>3||(slot->goal_mask&~3u))return DW_ARRAY;
 if(slot->has_reverse_goal!=!!(slot->goal_mask&1)||slot->has_forward_goal!=!!(slot->goal_mask&2))return DW_ARRAY;
 if(!nonnegative(slot->position_bound_m)||!nonnegative(slot->heading_bound_rad)||
    !nonnegative(slot->usable_width_m)||!nonnegative(slot->usable_depth_m)||
    (slot->has_reverse_goal&&!dw_pose_finite(slot->reverse_goal))||
    (slot->has_forward_goal&&!dw_pose_finite(slot->forward_goal)))return DW_NUMERIC;
 for(size_t i=0;i<slot->corners_count;i++)if(!finite_point(slot->corners[i]))return DW_NUMERIC;
 if(slot->geometry_state==1){
  if(slot->corners_count!=4||!convex4(slot->corners)||slot->usable_width_m<=0||slot->usable_depth_m<=0)return DW_GEOMETRY;
 }else if(slot->goal_mask||slot->occupancy_state)return DW_ARRAY;
 return DW_OK;
}
static enum dw_result scene_validate(const xenova_drive_SceneAdmissionRef *scene){
 if(scene->admission_id.size!=16||scene->survey_id.size!=16||scene->coverage_profile_sha256.size!=32)return DW_ARRAY;
 if(!positive63(scene->admission_revision)||!positive63(scene->survey_revision))return DW_SOURCE;
 return scene->policy>1||(!scene->policy&&scene->allow_restore_after_mapper_restart)?DW_ARRAY:DW_OK;
}
static enum dw_result world_extra(const dw_world *w,size_t count){
 bool current=w->required_features==11||w->required_features==15;
 if(current&&w->minor!=2)return DW_VERSION;
 if(w->has_lane_model!=!!(w->required_features&DW_LANE_MODEL)||
    w->has_localization!=!!(w->required_features&DW_LOCALIZATION_BOUNDS)||
    w->has_parking_set!=!!(w->required_features&DW_PARKING_MODEL)||w->has_drivable_permission!=current||
    w->parking_slots_count>8||(!(w->required_features&DW_PARKING_MODEL)&&w->parking_slots_count))return DW_ARRAY;
 if(!current)return w->has_scene?DW_VERSION:DW_OK;
 if(w->drivable_permission.size!=(count+7)/8||
    ((count%8)&&(w->drivable_permission.bytes[count/8]>>(count%8))))return DW_ARRAY;
 enum dw_result rc=lane_validate(&w->lane_model);if(rc)return rc;
 const xenova_drive_MapLocalization *loc=&w->localization;
 if(!positive63(loc->registration_revision)||!positive63(loc->observed_mapper_us)||
    !nonnegative(loc->anchor_position_bound_m)||!nonnegative(loc->anchor_heading_bound_rad)||
    !nonnegative(loc->geometry_position_bound_m)||!nonnegative(loc->geometry_heading_bound_rad)||
    !nonnegative(loc->position_drift_bound_mps)||!nonnegative(loc->heading_drift_bound_rps))return DW_NUMERIC;
 if(loc->quality>1||(loc->quality&&!loc->valid_for_us)||loc->observed_mapper_us>w->completed_us)return DW_TIME;
 if(w->has_parking_set){
  const xenova_drive_ParkingSetMeta *set=&w->parking_set;
  if(!positive63(set->revision)||set->state>2||set->track_count_in_roi>64||
     set->track_count_in_roi<w->parking_slots_count||(set->capacity_limited&&set->complete)||
     (set->complete&&set->track_count_in_roi!=w->parking_slots_count))return DW_ARRAY;
 }
 bool retained=false;
 for(size_t i=0;i<w->parking_slots_count;i++){
  rc=slot_validate(&w->parking_slots[i]);if(rc)return rc;
  for(size_t j=0;j<i;j++)if(w->parking_slots[i].slot_id==w->parking_slots[j].slot_id)return DW_SOURCE;
  retained|=w->parking_slots[i].occupancy_state==2&&(w->parking_slots[i].source_kinds&4);
 }
 for(size_t i=0;i<count;i++)retained|=w->occupancy.bytes[i]<70&&(w->cell_flags.bytes[i]&3)==3&&!(w->cell_flags.bytes[i]&4);
 if(w->has_scene){rc=scene_validate(&w->scene);if(rc)return rc;if(w->scene.policy!=1)return DW_ARRAY;}
 return retained&&!w->has_scene?DW_SOURCE:DW_OK;
}
static dw_point boundary(const dw_world *w,size_t k){
 size_t n=w->reference_count;return k<n?w->left_boundary[k]:w->right_boundary[2*n-1-k];
}
static bool road_simple(const dw_world *w){
 size_t n=w->reference_count,edges=2*n;
 if(w->required_features&DW_LANE_MODEL){
  /* 새 basis는 station 축이며 hard domain의 두 chain은 차선의 좌우 대응점이 아니다. */
  if(!open_basis(w->reference,n))return false;
 }else{
  for(size_t k=0;k<n;k++){
   dw_point a=w->reference[k?k-1:0],b=w->reference[k?k:1];
   if(cross(a,b,w->left_boundary[k])<=1e-6f||cross(a,b,w->right_boundary[k])>=-1e-6f)return false;
  }
 }
 /* Bounded at 128 edges, once per received immutable map, not per tracker. */
 for(size_t i=0;i<edges;i++){
  dw_point a=boundary(w,i),b=boundary(w,(i+1)%edges);
  if(hypotf(a.x-b.x,a.y-b.y)<1e-6f)return false;
  for(size_t j=i+1;j<edges;j++){
   if(j==i+1||(i==0&&j==edges-1))continue;
   if(intersects(a,b,boundary(w,j),boundary(w,(j+1)%edges)))return false;
  }
 }
 return true;
}

/* Protobuf itself permits repeated singular fields. This small top-level audit
 * rejects that ambiguity before decode, without allocating or reading a stream
 * twice from the network. Repeated road/path fields remain legal. */
static bool varint(const uint8_t *p,size_t n,size_t *at,uint64_t *value) {
 *value=0;
 for(unsigned k=0;k<10;k++) {
  if(*at>=n)return false;
  uint8_t b=p[(*at)++];if(k==9&&(b&0xfe))return false;
  *value|=(uint64_t)(b&127)<<(7*k);if(!(b&128))return true;
 }
 return false;
}
/* 생성기의 max_size와 별개로 binary UUID/SHA의 정확한 payload 길이를 검사한다. */
static size_t exact_bytes(const pb_msgdesc_t *desc,unsigned field){
 if(desc==xenova_drive_SceneAdmissionRef_fields)return field==1||field==3?16:field==6?32:0;
 if(desc==xenova_drive_MissionExecution_fields)return field==1||field==6||field==17?16:0;
 if(desc==xenova_drive_MissionGrant_fields){
  if((field>=3&&field<=7)||field==9||field==14||field==18)return 16;
  return field==22||field==31?32:0;
 }
 if(desc==xenova_drive_MissionBudgetRequest_fields)return field==2||field==3||field==14?16:field==12||field==13?32:0;
 if(desc==xenova_drive_MissionBudgetReply_fields)return field==2?16:field==3?32:0;
 if(desc==xenova_drive_MapperClockProbe_fields)return field==7?16:0;
 if(desc==xenova_drive_MapperClockReply_fields)return field==5?16:0;
 if(desc==xenova_drive_SceneGrant_fields)return field==17?16:field==21||field==24?32:0;
 if(desc==xenova_drive_SurveySummary_fields)return field==2||field==3?16:field==15||field==16?32:0;
 if(desc==xenova_drive_TofObservation_fields)return field==25?16:field==17?128:field==18||field==19?64:0;
 return 0;
}
static size_t message_cap(const pb_msgdesc_t *desc){
 if(desc==xenova_drive_LaneModel_fields)return 1408;
 if(desc==xenova_drive_ParkingSlot_fields)return 256;
 if(desc==xenova_drive_MapLocalization_fields||desc==xenova_drive_SceneAdmissionRef_fields)return 96;
 if(desc==xenova_drive_ParkingSetMeta_fields)return 64;
 if(desc==xenova_drive_MissionBudgetRequest_fields)return 512;
 if(desc==xenova_drive_MissionBudgetReply_fields)return 1536;
 if(desc==xenova_drive_SurveySummary_fields)return 288;
 if(desc==xenova_drive_MissionGrant_fields||desc==xenova_drive_MapperClockProbe_fields||desc==xenova_drive_MapperClockReply_fields||
    desc==xenova_drive_SceneGrant_fields||desc==xenova_drive_TofObservation_fields)return 1024;
 return DW_WIRE_MAX;
}
static bool wire_valid(const uint8_t *p,size_t n,const pb_msgdesc_t *desc,unsigned depth) {
 if(depth>8||n>message_cap(desc))return false;
 size_t at=0,metadata=0;uint64_t seen=0;bool union_seen=false;
 while(at<n) {
  size_t start=at;
  uint64_t tag,size=0;if(!varint(p,n,&at,&tag)||!(tag>>3)||(tag>>3)>0x1fffffff)return false;
  unsigned field=(unsigned)(tag>>3),kind=(unsigned)(tag&7);
  pb_field_iter_t iter;bool known=pb_field_iter_begin(&iter,desc,NULL)&&pb_field_iter_find(&iter,field);
  if(known&&field<64&&PB_HTYPE(iter.type)!=PB_HTYPE_REPEATED) {
   if(seen&(UINT64_C(1)<<field))return false;
   seen|=UINT64_C(1)<<field;
  }
  if(known&&PB_HTYPE(iter.type)==PB_HTYPE_ONEOF){if(union_seen)return false;union_seen=true;}
  if(kind==0){
   if(!varint(p,n,&at,&size))return false;
   if(known&&((PB_LTYPE(iter.type)==PB_LTYPE_UVARINT&&
      (iter.data_size==8?size>INT64_MAX:size>UINT32_MAX))||
      (PB_LTYPE(iter.type)==PB_LTYPE_SVARINT&&iter.data_size==4&&size>UINT32_MAX)||
      (PB_LTYPE(iter.type)==PB_LTYPE_BOOL&&size>1)))return false;
   if(desc==xenova_drive_DriveState_fields&&field>=29){metadata+=at-start;if(metadata>4096)return false;}
   continue;
  }
  if(kind==1)size=8;
  else if(kind==5)size=4;
  else if(kind==2){if(!varint(p,n,&at,&size))return false;}
  else return false;
  if(size>n-at)return false;
  if(known&&PB_LTYPE(iter.type)==PB_LTYPE_BYTES){size_t exact=exact_bytes(desc,field);if(kind!=2||(exact&&size!=exact))return false;}
  if(known&&PB_LTYPE(iter.type)==PB_LTYPE_STRING){
   if(kind!=2||!size)return false;
   for(size_t i=0;i<(size_t)size;i++)if(p[at+i]<33||p[at+i]>126)return false;
  }
  if(known&&iter.submsg_desc&&(kind!=2||!wire_valid(p+at,(size_t)size,iter.submsg_desc,depth+1)))return false;
  at+=(size_t)size;
  if(desc==xenova_drive_DriveState_fields&&field>=29){metadata+=at-start;if(metadata>4096)return false;}
 }
 return true;
}

/* direct typed 입력이 wire audit보다 약한 경로가 되지 않도록 생성 descriptor를 읽는다. */
static bool typed_valid(const void *value,const pb_msgdesc_t *desc,unsigned depth){
 if(!value||depth>8)return false;
 pb_field_iter_t iter;
 if(!pb_field_iter_begin(&iter,desc,(void *)value))return false;
 do{
  if(PB_ATYPE(iter.type)!=PB_ATYPE_STATIC)return false;
  unsigned htype=PB_HTYPE(iter.type),ltype=PB_LTYPE(iter.type);
  if(htype==PB_HTYPE_OPTIONAL&&iter.pSize&&!*(const bool *)iter.pSize)continue;
  if(htype==PB_HTYPE_ONEOF&&(!iter.pSize||*(const pb_size_t *)iter.pSize!=iter.tag))continue;
  size_t count=htype==PB_HTYPE_REPEATED?*(const pb_size_t *)iter.pSize:1;
  if(count>iter.array_size)return false;
  for(size_t i=0;i<count;i++){
   const uint8_t *item=(const uint8_t *)iter.pData+i*iter.data_size;
   if(iter.submsg_desc){if(!typed_valid(item,iter.submsg_desc,depth+1))return false;}
   else if(ltype==PB_LTYPE_UVARINT&&iter.data_size==8){
    uint64_t number;memcpy(&number,item,sizeof(number));if(number>INT64_MAX)return false;
   }else if(ltype==PB_LTYPE_STRING){
    const uint8_t *end=memchr(item,0,iter.data_size);
    if(!end||end==item)return false;
    for(const uint8_t *p=item;p<end;p++)if(*p<33||*p>126)return false;
   }else if(ltype==PB_LTYPE_BYTES){
    pb_size_t size;memcpy(&size,item,sizeof(size));
    size_t exact=exact_bytes(desc,iter.tag);
    if(size>iter.data_size-offsetof(pb_bytes_array_t,bytes)||(exact&&size!=exact))return false;
   }
  }
 }while(pb_field_iter_next(&iter));
 return true;
}

enum dw_result dw_world_validate(const dw_world *w,const struct dw_trust *t,uint64_t now,uint64_t *age) {
 if(!w||!t||!age)return DW_DECODE;
 if(w->major!=1||!features(w->required_features))return DW_VERSION;
 if(!typed_valid(w,xenova_drive_WorldSnapshot_fields,0))return DW_ARRAY;
 if(!w->complete||w->truncated)return DW_ARRAY;
 if(!id(w->vehicle)||strcmp(w->vehicle,t->vehicle)||strcmp(w->mapper_boot,t->mapper_boot)||
    strcmp(w->mapper_instance,t->mapper_instance))return DW_SOURCE;
 if(!id(w->map_epoch)||!id(w->frame_epoch)||!id(w->odom_epoch)||!w->sequence||!w->observation_seq)return DW_SOURCE;
 if(strcmp(w->map_epoch,t->map_epoch)||strcmp(w->frame_epoch,t->frame_epoch)||strcmp(w->odom_epoch,t->odom_epoch))return DW_EPOCH;
 if(strcmp(w->configuration,t->configuration)||strcmp(w->calibration,t->calibration)||
    strcmp(w->primitive,t->primitive)||w->inflation_policy!=0)return DW_CONFIGURATION;
 if(w->width<2||w->height<2||w->width>64||w->height>64)return DW_ARRAY;
 size_t count=(size_t)w->width*w->height;
 if(w->occupancy.size!=count||w->cell_flags.size!=count||w->age_le32.size!=count*4)return DW_ARRAY;
 if(!isfinite(w->resolution_m)||w->resolution_m<.001f||w->resolution_m>10||
    !dw_pose_finite(w->origin)||!dw_pose_finite(w->reference_vehicle_pose))return DW_GEOMETRY;
 if(!id(w->reference_id)||!w->reference_revision||w->reference_count<2||w->reference_count>64||
    w->reference_count!=w->left_boundary_count||w->reference_count!=w->right_boundary_count)return DW_GEOMETRY;
 for(size_t k=0;k<w->reference_count;k++) {
  if(!finite_point(w->reference[k])||!finite_point(w->left_boundary[k])||!finite_point(w->right_boundary[k]))return DW_NUMERIC;
  if(k&&hypotf(w->reference[k].x-w->reference[k-1].x,w->reference[k].y-w->reference[k-1].y)<w->resolution_m*.1f)return DW_GEOMETRY;
 }
 if(!road_simple(w))return DW_GEOMETRY;
 bool road=false;unsigned allowed=(w->required_features&DW_ROAD_PRIORITY)?15u:7u;
 for(size_t k=0;k<count;k++) {
  if((w->occupancy.bytes[k]>100&&w->occupancy.bytes[k]!=255)||(w->cell_flags.bytes[k]&~allowed))return DW_ARRAY;
  road|=(w->cell_flags.bytes[k]&DW_CELL_ROAD)!=0;
 }
 if((w->required_features&DW_ROAD_PRIORITY)&&!road)return DW_ARRAY;
 enum dw_result extra=world_extra(w,count);if(extra)return extra;
 if(!w->reference_odom_seq||strcmp(w->clock_epoch,t->clock.epoch)||
    w->observed_us>w->completed_us||w->completed_us>w->published_us||
    w->published_us>INT64_MAX||now>INT64_MAX||now>t->clock.expires_pi_us||
    t->clock.lower_offset_us>t->clock.upper_offset_us)return DW_TIME;
 int64_t lower=t->clock.lower_offset_us,upper=t->clock.upper_offset_us;
 if(lower>0&&(uint64_t)lower>(uint64_t)INT64_MAX-w->published_us)return DW_TIME;
 if(upper>0&&(uint64_t)upper>(uint64_t)INT64_MAX-w->published_us)return DW_TIME;
 if((uint64_t)upper-(uint64_t)lower>t->clock.max_error_us)return DW_TIME;
 int64_t observed=(int64_t)w->observed_us+lower,published=(int64_t)w->published_us+lower;
 uint32_t capture_error=w->has_localization?w->localization.timestamp_error_us:0;
 if(observed<(int64_t)capture_error||published<0||published>(int64_t)now)return DW_TIME;
 observed-=(int64_t)capture_error;
 *age=now-(uint64_t)observed;return DW_OK;
}
enum dw_result dw_decode(const uint8_t *p,size_t n,const struct dw_trust *t,uint64_t now,struct dw_snapshot *out) {
 if(n>DW_WIRE_MAX)return DW_TOO_LARGE;
 if(!p||!t||!out||!wire_valid(p,n,xenova_drive_WorldSnapshot_fields,0))return DW_DECODE;
 memset(out,0,sizeof(*out));pb_istream_t stream=pb_istream_from_buffer(p,n);
 if(!pb_decode(&stream,xenova_drive_WorldSnapshot_fields,&out->world))return DW_DECODE;
 enum dw_result rc=dw_world_validate(&out->world,t,now,&out->observed_upper_age_us);
 if(!rc){out->received_us=now;out->valid_until_us=t->clock.expires_pi_us;}
 return rc;
}
enum dw_result dw_snapshot_time(const struct dw_snapshot *map,uint64_t now,uint64_t max_age,uint64_t *deadline){
 if(!map||map->observed_upper_age_us>map->received_us||map->world.published_us<map->world.observed_us)return DW_TIME;
 uint64_t observed=map->received_us-map->observed_upper_age_us;
 uint64_t publication_delay=map->world.published_us-map->world.observed_us;
 uint64_t capture_error=map->world.has_localization?map->world.localization.timestamp_error_us:0;
 if(capture_error>UINT64_MAX-publication_delay)return DW_TIME;
 publication_delay+=capture_error;
 if(publication_delay>UINT64_MAX-observed||max_age>UINT64_MAX-observed)return DW_TIME;
 uint64_t end=observed+max_age;
 if(end>map->valid_until_us)end=map->valid_until_us;
 if(now<observed+publication_delay||now>end)return DW_TIME;
 if(deadline)*deadline=end;
 return DW_OK;
}
static enum dw_result vehicle_validate(const dw_vehicle *out){
 if(out->major!=1||(out->quality&~3))return DW_VERSION;
 if(!out->sequence||!out->source_sample||!id(out->vehicle)||!id(out->host_boot)||
    !id(out->device_session)||!id(out->odom_epoch)||!id(out->calibration))return DW_SOURCE;
 if(!dw_pose_finite(out->odom_pose)||!isfinite(out->velocity_mps)||
    (out->has_measured_steering_rad&&!isfinite(out->measured_steering_rad))||
    (out->has_estimated_steering_rad&&!isfinite(out->estimated_steering_rad))||
    (out->has_steering_error_rad&&(!isfinite(out->steering_error_rad)||out->steering_error_rad<0))||
    !isfinite(out->position_error_m)||out->position_error_m<0||
    !isfinite(out->heading_error_rad)||out->heading_error_rad<0)return DW_NUMERIC;
 return out->quality==3?DW_OK:DW_TIME;
}
enum dw_result dw_vehicle_decode(const uint8_t *p,size_t n,dw_vehicle *out) {
 if(n>2048)return DW_TOO_LARGE;
 if(!p||!out||!wire_valid(p,n,xenova_drive_VehicleSample_fields,0))return DW_DECODE;
 memset(out,0,sizeof(*out));pb_istream_t stream=pb_istream_from_buffer(p,n);
 if(!pb_decode(&stream,xenova_drive_VehicleSample_fields,out))return DW_DECODE;
 return vehicle_validate(out);
}
static enum dw_result mission_validate(const xenova_drive_MissionExecution *m){
 if(m->mission_id.size!=16||m->operator_request_nonce.size!=16||m->scene_admission_id.size!=16||
    m->selected_slot_polygon_count>4||m->kind>2||m->strategy>2||m->correction_attempts_used>2||m->result>3)return DW_ARRAY;
 if(!scene_pair(m->scene_admission_id.bytes,m->scene_admission_revision))return DW_SOURCE;
 if(!nonnegative(m->execution_travel_m)||!nonnegative(m->search_travel_m))return DW_NUMERIC;
 if(!m->kind){if(!nil_uuid(m->mission_id.bytes)||m->mission_revision||m->phase)return DW_ARRAY;}
 else if(nil_uuid(m->mission_id.bytes)||!positive63(m->mission_revision)||!positive63(m->grant_revision))return DW_SOURCE;
 if(m->kind!=2){
  if(m->strategy||m->mission_deadline_pi_us||m->execution_deadline_pi_us||m->correction_attempts_used||
     m->execution_travel_m||m->search_travel_m||m->search_deadline_pi_us)return DW_ARRAY;
  if(m->has_slot_id||m->has_slot_revision||m->has_goal||m->has_goal_revision||
     m->has_goal_position_bound_m||m->has_goal_heading_bound_rad||m->selected_slot_polygon_count)return DW_ARRAY;
  if(m->kind==1&&m->phase!=0&&m->phase!=10)return DW_ARRAY;
 }else{
  if(m->phase>9||!positive63(m->mission_deadline_pi_us)||!positive63(m->search_deadline_pi_us)||
     m->search_deadline_pi_us>m->mission_deadline_pi_us||m->execution_deadline_pi_us>m->mission_deadline_pi_us)return DW_TIME;
 }
 if(m->result==1&&(m->kind!=2||m->phase!=0))return DW_ARRAY;
 bool locked=m->kind==2&&m->phase>=3&&m->phase<=7;
 bool any=m->has_slot_id||m->has_slot_revision||m->has_goal||m->has_goal_revision||
          m->has_goal_position_bound_m||m->has_goal_heading_bound_rad||m->selected_slot_polygon_count;
 if(locked||any){
  if(!m->has_slot_id||!m->has_slot_revision||!m->has_goal||!m->has_goal_revision||
     !m->has_goal_position_bound_m||!m->has_goal_heading_bound_rad||m->selected_slot_polygon_count!=4)return DW_ARRAY;
  if(!m->slot_id||!positive63(m->slot_revision)||!positive63(m->goal_revision))return DW_SOURCE;
  if(!dw_pose_finite(m->goal)||!nonnegative(m->goal_position_bound_m)||!nonnegative(m->goal_heading_bound_rad))return DW_NUMERIC;
  if(!convex4(m->selected_slot_polygon))return DW_GEOMETRY;
 }
 return DW_OK;
}
static bool pose_equal(dw_pose a,dw_pose b){return a.x==b.x&&a.y==b.y&&a.yaw==b.yaw;}
static enum dw_result segments_validate(const dw_state *state){
 if(state->segments_count>3)return DW_ARRAY;
 if(!state->segments_count){
  if(state->mission.active_segment)return DW_ARRAY;
  for(size_t i=0;i<state->path_count;i++)if(state->path[i].velocity_mps)return DW_ARRAY;
  return DW_OK;
 }
 if(state->mission.active_segment>=state->segments_count)return DW_ARRAY;
 bool boundaries[512]={0};
 for(size_t i=0;i<state->segments_count;i++){
  const xenova_drive_GearSegment *segment=&state->segments[i];
  if((segment->signed_direction!=1&&segment->signed_direction!=-1)||segment->last_index>=state->path_count||
     segment->last_index<=segment->first_index||segment->first_index!=(i?state->segments[i-1].last_index+1:0))return DW_ARRAY;
  if(state->mission.kind==1&&segment->signed_direction!=1)return DW_ARRAY;
  float travel=0;
  for(size_t j=segment->first_index;j<=segment->last_index;j++){
   if(state->path[j].velocity_mps*segment->signed_direction<0)return DW_ARRAY;
   if(j>segment->first_index)travel+=hypotf(state->path[j].pose.x-state->path[j-1].pose.x,state->path[j].pose.y-state->path[j-1].pose.y);
  }
  if(!(travel>0))return DW_GEOMETRY;
  if(i){
   const xenova_drive_GearSegment *previous=&state->segments[i-1];
   const xenova_drive_PathPoint *before=&state->path[previous->last_index],*after=&state->path[segment->first_index];
   if(segment->signed_direction==previous->signed_direction||!pose_equal(before->pose,after->pose)||
      before->velocity_mps||after->velocity_mps)return DW_GEOMETRY;
   boundaries[segment->first_index]=true;
  }
 }
 if(state->segments[state->segments_count-1].last_index!=(uint32_t)state->path_count-1u)return DW_ARRAY;
 for(size_t i=0;i<state->path_count;i++)for(size_t j=0;j<i;j++){
  if(pose_equal(state->path[i].pose,state->path[j].pose)&&!(boundaries[i]&&j==i-1))return DW_GEOMETRY;
 }
 return DW_OK;
}
static enum dw_result grant_validate(const xenova_drive_MissionGrant *m){
 if(m->major!=1)return DW_VERSION;
 if(m->pi_host_boot.size!=16||m->gateway_instance.size!=16||m->owner_vm.size!=16||m->owner_execution.size!=16||
    m->app_instance.size!=16||m->mission_id.size!=16||m->operator_request_nonce.size!=16||m->scene_admission_id.size!=16||
    m->primitive.size!=32||(m->has_locked_goal_sha256&&m->locked_goal_sha256.size!=32)||
    m->kind>2||m->strategy>2||m->correction_attempts_used>2)return DW_ARRAY;
 if(!id(m->vehicle)||!id(m->configuration)||!id(m->calibration)||!m->control_generation||!positive63(m->issued_pi_us)||
    !scene_pair(m->scene_admission_id.bytes,m->scene_revision))return DW_SOURCE;
 if(!nonnegative(m->execution_travel_m)||!nonnegative(m->search_travel_m)||
    !nonnegative(m->max_execution_travel_m)||!nonnegative(m->max_search_travel_m)||
    m->max_execution_travel_m>5||m->max_search_travel_m>10)return DW_NUMERIC;
 if(!m->kind){if(!nil_uuid(m->mission_id.bytes)||m->mission_revision||m->admitted)return DW_ARRAY;}
 else if(nil_uuid(m->mission_id.bytes)||!positive63(m->mission_revision)||!positive63(m->grant_revision))return DW_SOURCE;
 if(m->kind!=2){
  if(m->strategy||m->mission_deadline_pi_us||m->execution_deadline_pi_us||m->correction_attempts_used||m->execution_travel_m||
     m->search_travel_m||m->search_deadline_pi_us||m->max_execution_travel_m||m->max_search_travel_m||m->has_locked_goal_sha256)return DW_ARRAY;
 }else if(!positive63(m->mission_deadline_pi_us)||!positive63(m->search_deadline_pi_us)||
    m->search_deadline_pi_us>m->mission_deadline_pi_us||m->execution_deadline_pi_us>m->mission_deadline_pi_us||
    m->has_locked_goal_sha256!=!!m->execution_deadline_pi_us)return DW_TIME;
 return DW_OK;
}
static enum dw_result budget_request_validate(const xenova_drive_MissionBudgetRequest *m){
 if(m->major!=1)return DW_VERSION;
 if(m->request_nonce.size!=16||m->mission_id.size!=16||m->plan_sha256.size!=32||m->selected_goal_sha256.size!=32||
    m->scene_admission_id.size!=16||m->action<1||m->action>2)return DW_ARRAY;
 if(nil_uuid(m->mission_id.bytes)||!positive63(m->mission_revision)||!m->control_generation||!positive63(m->expected_grant_revision)||
    !m->slot_id||!positive63(m->slot_revision)||!positive63(m->goal_revision)||!positive63(m->plan_revision)||
    !scene_pair(m->scene_admission_id.bytes,m->scene_revision))return DW_SOURCE;
 return positive63(m->completed_guest_us)&&positive63(m->valid_until_pi_us)?DW_OK:DW_TIME;
}
static enum dw_result budget_reply_validate(const xenova_drive_MissionBudgetReply *m){
 if(m->major!=1)return DW_VERSION;
 if(m->request_nonce.size!=16||m->request_sha256.size!=32||m->result<1||m->result>5||m->reason>10||
    m->has_grant!=(m->result==1)||(m->has_grant&&m->grant.kind!=2))return DW_ARRAY;
 return m->has_grant?grant_validate(&m->grant):DW_OK;
}
static enum dw_result state_validate(const dw_state *out) {
 if(out->major!=2||!features(out->world_features)||!out->has_state_features||out->state_features!=DW_STATE_FEATURES)return DW_VERSION;
 if(!out->has_mission||!out->complete||out->path_count<2||out->path_count>512||out->tracker_index>=out->path_count||
    !out->source_generation||!out->state_revision||!out->path_revision||!out->maneuver_revision||
    !out->vehicle_sequence||!out->calculation_sequence||out->direction< -1||out->direction>1||out->maneuver_phase>2)return DW_ARRAY;
 if(!id(out->source_vm)||!id(out->source_execution)||!id(out->source_app_instance)||
    !id(out->frame_epoch)||!id(out->odom_epoch)||!id(out->configuration)||!id(out->primitive)||
    !id(out->reference_id)||!out->reference_revision)return DW_SOURCE;
 if(!id(out->message_id)||!id(out->calibration)||!id(out->map_epoch)||!out->observation_seq)return DW_SOURCE;
 if(!dw_pose_finite(out->merge_goal))return DW_NUMERIC;
 if(out->has_merge_passed_reference_revision&&(!out->direction||!out->merge_passed_reference_revision||
    out->merge_passed_reference_revision!=out->reference_revision))return DW_ARRAY;
 for(size_t k=0;k<out->path_count;k++) {
  const xenova_drive_PathPoint *point=&out->path[k];
  if(!dw_pose_finite(point->pose)||!isfinite(point->steering_rad)||!isfinite(point->velocity_mps))return DW_NUMERIC;
 }
 enum dw_result rc=mission_validate(&out->mission);if(rc)return rc;
 if(out->has_direction_gate){
  const xenova_drive_DirectionGateRef *gate=&out->direction_gate;
  if(gate->state>4||gate->requested_direction< -1||gate->requested_direction>1||
     (gate->state&&(!positive63(gate->gate_revision)||!positive63(gate->stop_command_sequence))))return DW_ARRAY;
 }
 return segments_validate(out);
}

enum dw_result dw_state_validate(const dw_state *value){
 return typed_valid(value,xenova_drive_DriveState_fields,0)?state_validate(value):DW_ARRAY;
}
enum dw_result dw_mission_grant_validate(const xenova_drive_MissionGrant *value){
 return typed_valid(value,xenova_drive_MissionGrant_fields,0)?grant_validate(value):DW_ARRAY;
}
enum dw_result dw_budget_request_validate(const xenova_drive_MissionBudgetRequest *value){
 return typed_valid(value,xenova_drive_MissionBudgetRequest_fields,0)?budget_request_validate(value):DW_ARRAY;
}
enum dw_result dw_budget_reply_validate(const xenova_drive_MissionBudgetReply *value){
 return typed_valid(value,xenova_drive_MissionBudgetReply_fields,0)?budget_reply_validate(value):DW_ARRAY;
}
enum dw_result dw_state_decode(const uint8_t *p,size_t n,dw_state *out) {
 if(n>DW_WIRE_MAX)return DW_TOO_LARGE;
 if(!p||!out||!wire_valid(p,n,xenova_drive_DriveState_fields,0))return DW_DECODE;
 memset(out,0,sizeof(*out));pb_istream_t stream=pb_istream_from_buffer(p,n);
 if(!pb_decode(&stream,xenova_drive_DriveState_fields,out))return DW_DECODE;
 return state_validate(out);
}
enum dw_result dw_link_decode(const uint8_t *p,size_t n,xenova_drive_DriveLink *out) {
 if(n>DW_WIRE_MAX)return DW_TOO_LARGE;
 if(!p||!out||!wire_valid(p,n,xenova_drive_DriveLink_fields,0))return DW_DECODE;
 memset(out,0,sizeof(*out));pb_istream_t stream=pb_istream_from_buffer(p,n);
 if(!pb_decode(&stream,xenova_drive_DriveLink_fields,out))return DW_DECODE;
 if(out->major!=2)return DW_VERSION;
 if(out->which_payload==xenova_drive_DriveLink_snapshot_tag)return state_validate(&out->payload.snapshot);
 if(out->which_payload==xenova_drive_DriveLink_accepted_tag)return state_validate(&out->payload.accepted.state);
 if(out->which_payload==xenova_drive_DriveLink_clock_tag){
  if(out->payload.clock.pi_receive_us>out->payload.clock.pi_send_us)return DW_TIME;
  enum dw_result rc=vehicle_validate(&out->payload.clock.vehicle);if(rc)return rc;
  return out->payload.clock.has_current_mission?grant_validate(&out->payload.clock.current_mission):DW_OK;
 }
 if(out->which_payload==xenova_drive_DriveLink_mission_grant_tag)return grant_validate(&out->payload.mission_grant);
 if(out->which_payload==xenova_drive_DriveLink_budget_request_tag)return budget_request_validate(&out->payload.budget_request);
 if(out->which_payload==xenova_drive_DriveLink_budget_reply_tag)return budget_reply_validate(&out->payload.budget_reply);
 if(out->which_payload==xenova_drive_DriveLink_applied_tag){
  const xenova_drive_AppliedState *m=&out->payload.applied;
  if(m->rebuilt==m->has_copy_message_id||!m->application_sequence||
     (m->applied&&(!m->calculation_sequence||!m->vehicle_sequence||!m->maneuver_revision)))return DW_ARRAY;
 }
 return out->which_payload>=2&&out->which_payload<=6?DW_OK:DW_VERSION;
}

static bool lock(struct dw_pool *p) {
 if(!atomic_flag_test_and_set_explicit(&p->lock,memory_order_acquire))return true;
 atomic_fetch_add(&p->contention,1);return false;
}
static void unlock(struct dw_pool *p) { atomic_flag_clear_explicit(&p->lock,memory_order_release); }
void dw_pool_init(struct dw_pool *p) { memset(p,0,sizeof(*p));atomic_flag_clear(&p->lock);p->current=-1; }
enum dw_result dw_pool_source(struct dw_pool *p,const struct dw_trust *t) {
 if(!lock(p))return DW_BUSY;
 if(p->source_revision==UINT32_MAX){unlock(p);return DW_NO_SPACE;}
 p->trust=*t;p->authorized=true;p->source_revision++;unlock(p);return DW_OK;
}
static bool trust_id(const char *value,size_t capacity){
 return value[0]&&memchr(value,0,capacity)!=NULL;
}
static enum dw_result trust_valid(const struct dw_trust *t){
 if(!t)return DW_SOURCE;
 if(!trust_id(t->vehicle,sizeof(t->vehicle))||!trust_id(t->mapper_boot,sizeof(t->mapper_boot))||
    !trust_id(t->mapper_instance,sizeof(t->mapper_instance))||!trust_id(t->map_epoch,sizeof(t->map_epoch))||
    !trust_id(t->frame_epoch,sizeof(t->frame_epoch))||!trust_id(t->odom_epoch,sizeof(t->odom_epoch))||
    !trust_id(t->configuration,sizeof(t->configuration))||!trust_id(t->calibration,sizeof(t->calibration))||
    !trust_id(t->primitive,sizeof(t->primitive))||!trust_id(t->clock.epoch,sizeof(t->clock.epoch)))return DW_SOURCE;
 if(!t->max_live_age_us||t->max_live_age_us>INT64_MAX)return DW_CONFIGURATION;
 if(!t->clock.expires_pi_us||t->clock.expires_pi_us>INT64_MAX||
    t->clock.lower_offset_us>t->clock.upper_offset_us||
    (uint64_t)t->clock.upper_offset_us-(uint64_t)t->clock.lower_offset_us>t->clock.max_error_us)return DW_TIME;
 return DW_OK;
}
static bool same_source(const struct dw_trust *a,const struct dw_trust *b){
 /* 구조체 padding과 각 문자열의 NUL 뒤 bytes는 신원이 아니다. */
 return !strcmp(a->vehicle,b->vehicle)&&!strcmp(a->mapper_boot,b->mapper_boot)&&
  !strcmp(a->mapper_instance,b->mapper_instance)&&!strcmp(a->map_epoch,b->map_epoch)&&
  !strcmp(a->frame_epoch,b->frame_epoch)&&!strcmp(a->odom_epoch,b->odom_epoch)&&
  !strcmp(a->configuration,b->configuration)&&!strcmp(a->calibration,b->calibration)&&
  !strcmp(a->primitive,b->primitive)&&!strcmp(a->clock.epoch,b->clock.epoch)&&
  a->max_live_age_us==b->max_live_age_us&&a->clock.max_error_us==b->clock.max_error_us;
}
enum dw_result dw_pool_refresh(struct dw_pool *p,const struct dw_trust *t){
 if(!p)return DW_SOURCE;
 enum dw_result rc=trust_valid(t);if(rc)return rc;
 if(!lock(p))return DW_BUSY;
 /* force API로 설치된 과거 trust도 bounded 문자열인지 확인한 뒤 비교한다. */
 bool unchanged=p->authorized&&!trust_valid(&p->trust)&&same_source(&p->trust,t);
 if(!unchanged&&p->source_revision==UINT32_MAX){unlock(p);return DW_NO_SPACE;}
 p->trust=*t;p->authorized=true;if(!unchanged)p->source_revision++;
 unlock(p);return DW_OK;
}
static bool handle(struct dw_pool *p,struct dw_handle h) { return h.slot<DW_MAP_SLOTS&&p->slots[h.slot].generation==h.generation; }
enum dw_result dw_reserve(struct dw_pool *p,struct dw_handle *h,struct dw_snapshot **out) {
 if(!lock(p))return DW_BUSY;
 enum dw_result rc=DW_NO_SPACE;
 if(!p->authorized){unlock(p);return DW_SOURCE;}
 for(unsigned k=0;k<DW_MAP_SLOTS;k++) {
  struct dw_slot *slot=&p->slots[k];
  if((int)k==p->current||slot->writing||slot->readers)continue;
  if(p->next_generation==UINT32_MAX){rc=DW_NO_SPACE;break;}
  slot->generation=++p->next_generation;slot->writing=true;slot->source_revision=p->source_revision;
  unsigned used=0;for(unsigned i=0;i<DW_MAP_SLOTS;i++)if((int)i==p->current||p->slots[i].writing||p->slots[i].readers)used++;
  if(used>p->high_water)p->high_water=used;
  *h=(struct dw_handle){slot->generation,(uint8_t)k,0};*out=&slot->snapshot;rc=DW_OK;break;
 }
 if(rc){p->busy++;if(rc==DW_NO_SPACE&&p->next_generation!=UINT32_MAX)p->exhausted++;}
 unlock(p);return rc;
}
enum dw_result dw_publish(struct dw_pool *p,struct dw_handle h) {
 if(!lock(p))return DW_BUSY;
 enum dw_result rc=DW_HANDLE;
 if(handle(p,h)&&p->slots[h.slot].writing&&!h.lease) {
  const dw_world *next=&p->slots[h.slot].snapshot.world;
  const dw_world *current=p->current<0?NULL:&p->slots[p->current].snapshot.world;
  if(p->slots[h.slot].source_revision!=p->source_revision||strcmp(next->mapper_boot,p->trust.mapper_boot)||
     strcmp(next->mapper_instance,p->trust.mapper_instance)||strcmp(next->map_epoch,p->trust.map_epoch))rc=DW_SOURCE;
  else if(current&&(!strcmp(current->mapper_boot,next->mapper_boot))&&
     (!strcmp(current->mapper_instance,next->mapper_instance))&&
     (!strcmp(current->map_epoch,next->map_epoch))&&next->sequence<=current->sequence)rc=DW_STALE;
  else { p->current=h.slot;p->slots[h.slot].writing=false;p->published++;rc=DW_OK; }
 }
 if(rc)p->rejected++;
 unlock(p);return rc;
}
enum dw_result dw_abort(struct dw_pool *p,struct dw_handle h) {
 if(!lock(p))return DW_BUSY;
 enum dw_result rc=DW_HANDLE;
 if(handle(p,h)&&p->slots[h.slot].writing&&!h.lease){p->slots[h.slot].writing=false;rc=DW_OK;}
 unlock(p);return rc;
}
enum dw_result dw_acquire(struct dw_pool *p,struct dw_handle *h,const struct dw_snapshot **out) {
 if(!lock(p))return DW_BUSY;
 enum dw_result rc=DW_NO_REFERENCE;
 if(p->current>=0) {
  struct dw_slot *slot=&p->slots[p->current];
  if(slot->source_revision!=p->source_revision)rc=DW_SOURCE;
  else if(slot->readers==8||p->next_lease==UINT32_MAX)rc=DW_NO_SPACE;
  else {
   for(unsigned k=0;k<8;k++)if(!slot->leases[k]) {
    slot->leases[k]=++p->next_lease;slot->readers++;
    *h=(struct dw_handle){slot->generation,(uint8_t)p->current,slot->leases[k]};
    *out=&slot->snapshot;rc=DW_OK;break;
   }
  }
 }
 unlock(p);return rc;
}
enum dw_result dw_source_valid(struct dw_pool *p,struct dw_handle h){
 if(!lock(p))return DW_BUSY;
 enum dw_result rc=handle(p,h)&&p->slots[h.slot].source_revision==p->source_revision?DW_OK:DW_SOURCE;
 unlock(p);return rc;
}

enum dw_result dw_release(struct dw_pool *p,struct dw_handle h) {
 if(handle(p,h)&&h.lease) {
  struct dw_slot *slot=&p->slots[h.slot];
  for(unsigned k=0;k<8;k++){
   unsigned expected=h.lease;
   if(atomic_compare_exchange_strong(&slot->leases[k],&expected,0)){atomic_fetch_sub(&slot->readers,1);return DW_OK;}
  }
 }
 return DW_HANDLE;
}
void dw_odom_init(struct dw_odometry *h) {memset(h,0,sizeof(*h));atomic_flag_clear(&h->lock);}
enum dw_result dw_odom_reset(struct dw_odometry *h) {
 if(atomic_flag_test_and_set_explicit(&h->lock,memory_order_acquire))return DW_BUSY;
 h->next=h->count=0;atomic_flag_clear_explicit(&h->lock,memory_order_release);return DW_OK;
}
enum dw_result dw_vehicle_latest(struct dw_odometry *h,dw_vehicle *out){
 if(atomic_flag_test_and_set_explicit(&h->lock,memory_order_acquire))return DW_BUSY;
 enum dw_result rc=h->count?DW_OK:DW_NO_REFERENCE;
 if(!rc)*out=h->samples[(h->next+DW_ODOM_SLOTS-1)%DW_ODOM_SLOTS];
 atomic_flag_clear_explicit(&h->lock,memory_order_release);return rc;
}
static enum dw_result odom_add(struct dw_odometry *h,const dw_vehicle *v) {
 if(h->count) {
  const dw_vehicle *last=&h->samples[(h->next+DW_ODOM_SLOTS-1)%DW_ODOM_SLOTS];
  if(strcmp(last->odom_epoch,v->odom_epoch)||strcmp(last->device_session,v->device_session)||strcmp(last->host_boot,v->host_boot))return DW_EPOCH;
  if(v->sequence<=last->sequence||v->pi_sample_us<=last->pi_sample_us||v->source_sample<=last->source_sample||v->source_us<=last->source_us)return DW_STALE;
 }
 h->samples[h->next]=*v;h->next=(h->next+1)%DW_ODOM_SLOTS;if(h->count<DW_ODOM_SLOTS)h->count++;
 if(h->count>h->high_water)h->high_water=h->count;
 return DW_OK;
}
enum dw_result dw_odom_add(struct dw_odometry *h,const dw_vehicle *v) {
 if(atomic_flag_test_and_set_explicit(&h->lock,memory_order_acquire))return DW_BUSY;
 enum dw_result rc=odom_add(h,v);atomic_flag_clear_explicit(&h->lock,memory_order_release);return rc;
}
static enum dw_result odom_pair(const dw_world *w,struct dw_odometry *h,dw_vehicle *last,dw_pose *anchor) {
 if(!h->count)return DW_NO_REFERENCE;
 *last=h->samples[(h->next+DW_ODOM_SLOTS-1)%DW_ODOM_SLOTS];
 for(size_t k=0;k<h->count;k++)if(h->samples[k].sequence==w->reference_odom_seq&&!strcmp(h->samples[k].odom_epoch,w->odom_epoch)){
  *anchor=h->samples[k].odom_pose;return DW_OK;
 }
 return DW_NO_REFERENCE;
}
enum dw_result dw_current_pose(const dw_world *w,struct dw_odometry *h,uint64_t now,uint64_t max_age,dw_pose *out,dw_vehicle *vehicle) {
 if(atomic_flag_test_and_set_explicit(&h->lock,memory_order_acquire))return DW_BUSY;
 dw_vehicle sample;dw_pose anchor;enum dw_result rc=odom_pair(w,h,&sample,&anchor);
 atomic_flag_clear_explicit(&h->lock,memory_order_release);if(rc)return rc;
 const dw_vehicle *last=&sample;
 if(strcmp(last->odom_epoch,w->odom_epoch)||strcmp(last->calibration,w->calibration)||strcmp(last->vehicle,w->vehicle))return DW_EPOCH;
 if(now<last->pi_sample_us||now-last->pi_sample_us+last->clock_error_us>max_age||last->quality!=3)return DW_TIME;
 *out=dw_compose(w->reference_vehicle_pose,dw_compose(dw_inverse(anchor),last->odom_pose));
 if(!dw_pose_finite(*out))return DW_NUMERIC;
 if(vehicle)*vehicle=*last;
 return DW_OK;
}
const char *dw_reason(enum dw_result rc) {
 static const char *const names[]={"OK","BUSY","TOO_LARGE","DECODE","VERSION","SOURCE","ARRAY","GEOMETRY", "TIME","CONFIGURATION","STALE","NO_REFERENCE","EPOCH","HANDLE","NO_SPACE","NUMERIC"};
 return (unsigned)rc<sizeof(names)/sizeof(*names)?names[rc]:"INTERNAL_ERROR";
}

enum dw_result dw_memory_read(struct dw_pool *p,struct dw_memory *out){
 if(atomic_flag_test_and_set_explicit(&p->lock,memory_order_acquire))return DW_BUSY;
 unsigned used=0;
 for(unsigned i=0;i<DW_MAP_SLOTS;i++)
  if((int)i==p->current||p->slots[i].writing||atomic_load(&p->slots[i].readers))used++;
 *out=(struct dw_memory){.reserved=sizeof(*p),.capacity=DW_MAP_SLOTS*sizeof(struct dw_snapshot),
  .used=used*sizeof(struct dw_snapshot),.peak=p->high_water*sizeof(struct dw_snapshot),
  .exhausted=p->exhausted,.contention=atomic_load(&p->contention)};
 unlock(p);return DW_OK;
}
enum dw_result dw_odom_memory_read(struct dw_odometry *p,struct dw_memory *out){
 if(atomic_flag_test_and_set_explicit(&p->lock,memory_order_acquire))return DW_BUSY;
 *out=(struct dw_memory){.reserved=sizeof(*p),.capacity=sizeof(p->samples),
  .used=p->count*sizeof(p->samples[0]),.peak=p->high_water*sizeof(p->samples[0])};
 atomic_flag_clear_explicit(&p->lock,memory_order_release);return DW_OK;
}
