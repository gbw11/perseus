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
static dw_point boundary(const dw_world *w,size_t k){
 size_t n=w->reference_count;return k<n?w->left_boundary[k]:w->right_boundary[2*n-1-k];
}
static bool road_simple(const dw_world *w){
 size_t n=w->reference_count,edges=2*n;
 for(size_t k=0;k<n;k++){
  dw_point a=w->reference[k?k-1:0],b=w->reference[k?k:1];
  if(cross(a,b,w->left_boundary[k])<=1e-6f||cross(a,b,w->right_boundary[k])>=-1e-6f)return false;
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
static bool wire_valid(const uint8_t *p,size_t n,const pb_msgdesc_t *desc,unsigned depth) {
 if(depth>8)return false;
 size_t at=0;uint64_t seen=0;bool union_seen=false;
 while(at<n) {
  uint64_t tag,size=0;if(!varint(p,n,&at,&tag)||!(tag>>3)||(tag>>3)>0x1fffffff)return false;
  unsigned field=(unsigned)(tag>>3),kind=(unsigned)(tag&7);
  pb_field_iter_t iter;bool known=pb_field_iter_begin(&iter,desc,NULL)&&pb_field_iter_find(&iter,field);
  if(known&&field<64&&PB_HTYPE(iter.type)!=PB_HTYPE_REPEATED) {
   if(seen&(UINT64_C(1)<<field))return false;
   seen|=UINT64_C(1)<<field;
  }
  if(known&&PB_HTYPE(iter.type)==PB_HTYPE_ONEOF){if(union_seen)return false;union_seen=true;}
  if(kind==0){if(!varint(p,n,&at,&size))return false;continue;}
  if(kind==1)size=8;
  else if(kind==5)size=4;
  else if(kind==2){if(!varint(p,n,&at,&size))return false;}
  else return false;
  if(size>n-at)return false;
  if(known&&PB_LTYPE(iter.type)==PB_LTYPE_STRING){
   if(kind!=2)return false;
   for(size_t i=0;i<(size_t)size;i++)if(p[at+i]<33||p[at+i]>126)return false;
  }
  if(known&&iter.submsg_desc&&(kind!=2||!wire_valid(p+at,(size_t)size,iter.submsg_desc,depth+1)))return false;
  at+=(size_t)size;
 }
 return true;
}

enum dw_result dw_world_validate(const dw_world *w,const struct dw_trust *t,uint64_t now,uint64_t *age) {
 if(w->major!=1||(w->required_features&~DW_ROAD_PRIORITY))return DW_VERSION;
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
 if(!id(w->reference_id)||!w->reference_revision||w->reference_count<2||
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
 if(!w->reference_odom_seq||strcmp(w->clock_epoch,t->clock.epoch)||
    w->observed_us>w->completed_us||w->completed_us>w->published_us||
    w->published_us>INT64_MAX||now>INT64_MAX||now>t->clock.expires_pi_us||
    t->clock.lower_offset_us>t->clock.upper_offset_us)return DW_TIME;
 int64_t lower=t->clock.lower_offset_us,upper=t->clock.upper_offset_us;
 if(lower>0&&(uint64_t)lower>(uint64_t)INT64_MAX-w->published_us)return DW_TIME;
 if(upper>0&&(uint64_t)upper>(uint64_t)INT64_MAX-w->published_us)return DW_TIME;
 if((uint64_t)upper-(uint64_t)lower>t->clock.max_error_us)return DW_TIME;
 int64_t observed=(int64_t)w->observed_us+lower,published=(int64_t)w->published_us+lower;
 if(observed<0||published<0||published>(int64_t)now)return DW_TIME;
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
static enum dw_result state_validate(const dw_state *out) {
 if(out->major!=1||(out->world_features&~DW_ROAD_PRIORITY))return DW_VERSION;
 if(!out->complete||out->path_count<2||out->tracker_index>=out->path_count||
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
 return DW_OK;
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
 if(out->major!=1)return DW_VERSION;
 if(out->which_payload==xenova_drive_DriveLink_snapshot_tag)return state_validate(&out->payload.snapshot);
 if(out->which_payload==xenova_drive_DriveLink_accepted_tag)return state_validate(&out->payload.accepted.state);
 if(out->which_payload==xenova_drive_DriveLink_clock_tag)return vehicle_validate(&out->payload.clock.vehicle);
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
