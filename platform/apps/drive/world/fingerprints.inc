/* Allocation-free shared fingerprints. No motion or registry authority. */
#include "../../common/xenova-drive-runtime/include/xenova/sha256.h"
#include <float.h>
_Static_assert(sizeof(float)==4&&FLT_MANT_DIG==24&&sizeof(double)==8&&DBL_MANT_DIG==53,"IEEE fingerprint scalars");
struct dhash {uint32_t h[8];uint8_t block[64];uint64_t bytes;size_t used;bool valid;};
static void dh_put(struct dhash *h,const void *data,size_t n){
 if(!h->valid||(!data&&n)||n>UINT64_MAX/8-h->bytes){h->valid=false;return;}
 const uint8_t *p=data;h->bytes+=n;
 while(n){size_t take=64-h->used;if(take>n)take=n;memcpy(h->block+h->used,p,take);h->used+=take;p+=take;n-=take;
  if(h->used==64){xdr_sha256_block(h->h,h->block);h->used=0;}}
}
static void dh_init(struct dhash *h,const char *domain,size_t n){
 static const uint32_t initial[8]={0x6a09e667u,0xbb67ae85u,0x3c6ef372u,0xa54ff53au,0x510e527fu,0x9b05688cu,0x1f83d9abu,0x5be0cd19u};
 memset(h,0,sizeof(*h));memcpy(h->h,initial,sizeof(initial));h->valid=true;dh_put(h,domain,n);
}
static bool dh_done(struct dhash *h,uint8_t out[32]){
 if(!out||!h->valid)return false;
 uint64_t bits=h->bytes*8;uint8_t tail[72]={0x80};size_t padding=h->used<56?56-h->used:120-h->used;
 for(unsigned i=0;i<8;i++)tail[padding+i]=(uint8_t)(bits>>(56-8*i));
 dh_put(h,tail,padding+8);if(!h->valid)return false;
 for(unsigned i=0;i<8;i++)for(unsigned j=0;j<4;j++)out[4*i+j]=(uint8_t)(h->h[i]>>(24-8*j));
 return true;
}
static void dh_u(struct dhash *h,uint64_t value,unsigned n){uint8_t raw[8];for(unsigned i=0;i<n;i++)raw[i]=(uint8_t)(value>>(8*i));dh_put(h,raw,n);}
static void dh_f(struct dhash *h,float value){uint32_t word;if(!isfinite(value)){h->valid=false;return;}memcpy(&word,&value,4);dh_u(h,word,4);}
static void dh_point(struct dhash *h,dw_point p){dh_f(h,p.x);dh_f(h,p.y);}
static void dh_pose(struct dhash *h,dw_pose p){dh_f(h,p.x);dh_f(h,p.y);dh_f(h,p.yaw);}
static void dh_text(struct dhash *h,const char *s,size_t cap){
 const char *end=memchr(s,0,cap);if(!end||end==s||(size_t)(end-s)>64){h->valid=false;return;}
 size_t n=(size_t)(end-s);for(size_t i=0;i<n;i++)if((unsigned char)s[i]<33||(unsigned char)s[i]>126){h->valid=false;return;}
 dh_u(h,n,2);dh_put(h,s,n);
}
static bool dh_pb_write(pb_ostream_t *s,const pb_byte_t *p,size_t n){struct dhash *h=s->state;dh_put(h,p,n);return h->valid;}
static bool dh_pb(const void *value,const pb_msgdesc_t *desc,const char *domain,size_t length,uint8_t out[32]){
 struct dhash hash;dh_init(&hash,domain,length);
 pb_ostream_t stream={.callback=dh_pb_write,.state=&hash,.max_size=DW_WIRE_MAX,.bytes_written=0};
 return pb_encode(&stream,desc,value)&&dh_done(&hash,out);
}
bool dw_episode_core_hash(const xenova_drive_EpisodeCore *c,uint8_t out[32]){
 return dw_episode_core_validate(c)==DW_OK&&dh_pb(c,xenova_drive_EpisodeCore_fields,"XNVECORE1",10,out);
}
bool dw_episode_source_hash(const xenova_drive_EpisodeSourceContext *s,uint8_t out[32]){
 if(dw_episode_source_validate(s))return false;
 xenova_drive_EpisodeSourceContext original=*s;original.reference_revision=original.world_sequence=original.observation_sequence=0;
 return dh_pb(&original,xenova_drive_EpisodeSourceContext_fields,"XNVESRC1",9,out);
}
bool dw_episode_request_hash(const xenova_drive_EpisodeRequest *r,uint8_t out[32]){
 return dw_episode_request_validate(r)==DW_OK&&dh_pb(r,xenova_drive_EpisodeRequest_fields,"",0,out);
}
bool dw_basis_prefix_hash(const dw_point *points,size_t count,uint8_t out[32]){
 if(!points||count<2||count>64)return false;
 struct dhash h;dh_init(&h,"XNVBASIS1",10);dh_u(&h,count,1);
 for(size_t i=0;i<count;i++)dh_point(&h,points[i]);
 return dh_done(&h,out);
}
bool dw_plan_hash(const dw_state *s,uint8_t out[32]){
 if(!s||s->path_count<2||s->path_count>512||s->segments_count>3)return false;
 struct dhash h;dh_init(&h,"XNVPLAN1",9);dh_u(&h,s->world_features,4);
#define EP_TEXT(object,key) dh_text(&h,(object)->key,sizeof((object)->key))
 EP_TEXT(s,map_epoch);EP_TEXT(s,frame_epoch);EP_TEXT(s,odom_epoch);EP_TEXT(s,configuration);EP_TEXT(s,calibration);EP_TEXT(s,primitive);EP_TEXT(s,reference_id);
 dh_u(&h,s->has_task_reference?s->task_reference.basis_revision:0,8);dh_u(&h,s->path_revision,8);dh_u(&h,s->maneuver_revision,8);dh_u(&h,s->path_count,2);
 for(size_t i=0;i<s->path_count;i++){dh_pose(&h,s->path[i].pose);dh_f(&h,s->path[i].steering_rad);dh_f(&h,s->path[i].velocity_mps);}
 dh_u(&h,s->segments_count,1);
 for(size_t i=0;i<s->segments_count;i++){const xenova_drive_GearSegment *g=&s->segments[i];
  if(g->first_index>=g->last_index||g->last_index>=s->path_count||(g->signed_direction!=-1&&g->signed_direction!=1))return false;
  dh_u(&h,g->first_index,2);dh_u(&h,g->last_index,2);dh_u(&h,(uint8_t)(int8_t)g->signed_direction,1);}
 dh_u(&h,s->has_task_reference,1);
 if(s->has_task_reference){const xenova_drive_TaskReferenceState *t=&s->task_reference;
  if(t->basis_prefix_count<2||t->basis_prefix_count>64)return false;
  dh_u(&h,t->home_lane_id,4);dh_u(&h,t->from_lane_id,4);dh_u(&h,t->selected_lane_id,4);dh_u(&h,t->changing,1);dh_u(&h,t->basis_revision,8);dh_u(&h,t->basis_prefix_count,1);
  for(size_t i=0;i<t->basis_prefix_count;i++)dh_point(&h,t->basis_prefix[i]);
  EP_TEXT(t,vehicle);EP_TEXT(t,mapper_boot);EP_TEXT(t,mapper_instance);EP_TEXT(t,clock_epoch);}
#undef EP_TEXT
 return dh_done(&h,out);
}
bool dw_selected_goal_hash(const xenova_drive_MissionExecution *m,uint8_t out[32]){
 if(!m||m->kind!=2||!m->has_slot_id||!m->has_slot_revision||!m->has_goal||!m->has_goal_revision||!m->has_goal_position_bound_m||!m->has_goal_heading_bound_rad||m->selected_slot_polygon_count!=4||m->mission_id.size!=16||m->scene_admission_id.size!=16)return false;
 struct dhash h;dh_init(&h,"XNVGOAL1",9);dh_put(&h,m->mission_id.bytes,16);dh_u(&h,m->mission_revision,8);
 dh_put(&h,m->scene_admission_id.bytes,16);dh_u(&h,m->scene_admission_revision,8);dh_u(&h,m->slot_id,4);dh_u(&h,m->slot_revision,8);dh_u(&h,m->goal_revision,8);dh_pose(&h,m->goal);dh_u(&h,m->selected_slot_polygon_count,1);
 for(size_t i=0;i<m->selected_slot_polygon_count;i++)dh_point(&h,m->selected_slot_polygon[i]);
 dh_f(&h,m->goal_position_bound_m);dh_f(&h,m->goal_heading_bound_rad);return dh_done(&h,out);
}
