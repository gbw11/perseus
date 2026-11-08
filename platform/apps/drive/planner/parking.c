/* SPDX-License-Identifier: Apache-2.0 */
#include "parking.h"
#include <float.h>
#include <math.h>
#include <string.h>

bool dp_limits_valid(const struct dp_limits *l){
 return l&&isfinite(l->goal_distance)&&l->goal_distance>0&&isfinite(l->goal_heading)&&l->goal_heading>0&&
  isfinite(l->seed_inward_m)&&l->seed_inward_m>0&&isfinite(l->yaw_seed_rad)&&l->yaw_seed_rad>0&&
  isfinite(l->min_control_speed)&&l->min_control_speed>0&&isfinite(l->clearance_band)&&l->clearance_band>0&&
  isfinite(l->clearance_sufficient)&&l->clearance_sufficient>=l->clearance_band&&l->final_window_us&&
  isfinite(l->stationary_speed)&&l->stationary_speed>0&&isfinite(l->yaw_rate)&&l->yaw_rate>0;
}
const xenova_drive_ParkingSlot *dp_slot(const dw_world *w,uint32_t id){
 if(!w||!id)return NULL;
 for(unsigned k=0;k<w->parking_slots_count;k++)if(w->parking_slots[k].slot_id==id)return &w->parking_slots[k];
 return NULL;
}
static float slot_distance(const xenova_drive_ParkingSlot *s,dw_pose pose){
 float x=0,y=0;for(unsigned k=0;k<4;k++){x+=s->corners[k].x*.25f;y+=s->corners[k].y*.25f;}
 return hypotf(x-pose.x,y-pose.y);
}
bool dp_round_begin(struct dp_round *r,const dw_world *w,const xenova_drive_MissionGrant *g,dw_pose pose){
 if(!r||!w||!g||g->kind!=2||!g->admitted||!(w->required_features&DW_PARKING_MODEL)||!dw_pose_finite(pose))return false;
 if(r->initialized){
  bool same=!strcmp(r->map,w->map_epoch)&&!strcmp(r->frame,w->frame_epoch)&&!strcmp(r->configuration,w->configuration)&&
   !strcmp(r->calibration,w->calibration)&&!strcmp(r->primitive,w->primitive)&&r->mission_revision==g->mission_revision&&
   !memcmp(r->mission_id,g->mission_id.bytes,16);
  if(same)return !r->done;
  return false; /* semantic replacement is an explicit stopped reevaluation */
 }
 memset(r,0,sizeof(*r));r->initialized=true;r->source_revision=w->parking_set.revision;
 strcpy(r->map,w->map_epoch);strcpy(r->frame,w->frame_epoch);strcpy(r->configuration,w->configuration);
 strcpy(r->calibration,w->calibration);strcpy(r->primitive,w->primitive);memcpy(r->mission_id,g->mission_id.bytes,16);r->mission_revision=g->mission_revision;
 for(unsigned k=0;k<w->parking_slots_count&&k<8;k++){
  const xenova_drive_ParkingSlot *s=&w->parking_slots[k];unsigned at=r->count;
  while(at){const xenova_drive_ParkingSlot *old=dp_slot(w,r->ids[at-1]);float a=slot_distance(s,pose),b=slot_distance(old,pose);
   if(a>b||(a==b&&s->slot_id>old->slot_id))break;
   r->ids[at]=r->ids[at-1];at--;}
  r->ids[at]=s->slot_id;r->count++;
 }
 r->done=!r->count;return !r->done;
}
uint32_t dp_round_next(const struct dp_round *r){
 if(!r||!r->initialized||r->done)return 0;
 for(unsigned k=0;k<r->count;k++)if(!(r->visited&(1u<<k)))return r->ids[k];
 return 0;
}
bool dp_round_visit(struct dp_round *r,uint32_t id){
 if(!r||!r->initialized||!id)return false;
 for(unsigned k=0;k<r->count;k++)if(r->ids[k]==id){r->visited|=1u<<k;r->done=r->visited==((1u<<r->count)-1);return true;}
 return false;
}
bool dp_slot_eligible(const dw_world *w,const xenova_drive_ParkingSlot *s,unsigned strategy){
 if(!w||!s||!(w->required_features&DW_PARKING_MODEL)||!w->has_parking_set||w->parking_set.state!=1||
    !s->geometry_complete||s->geometry_state!=1||s->occupancy_state!=2||s->corners_count!=4||s->entry_edge>=4||
    !s->geometry_revision||!s->occupancy_revision||!s->evidence_sequence||s->geometry_age_ms>500||s->clearance_age_ms>500)return false;
 return strategy==1?(s->goal_mask&1)!=0:strategy==2&&(s->goal_mask&2)!=0;
}
static float cross(dw_point a,dw_point b,dw_point p){return (b.x-a.x)*(p.y-a.y)-(b.y-a.y)*(p.x-a.x);}
bool dp_goal_contains(const struct lp_space *space,dw_pose pose,float pad,const struct lp_config *c){
 if(!space||!space->parking||!c||!dw_pose_finite(pose)||!isfinite(pad)||pad<0)return false;
 float extra=c->margin+space->uncertainty+pad,cs=cosf(pose.yaw),sn=sinf(pose.yaw);
 for(unsigned j=0;j<4;j++){
  float x=j&1?c->front+extra:-c->rear-extra,y=j&2?c->half_width+extra:-c->half_width-extra;
  dw_point p={pose.x+cs*x-sn*y,pose.y+sn*x+cs*y};float sign=0;
  for(unsigned k=0;k<4;k++){float v=cross(space->polygon[k],space->polygon[(k+1)%4],p);
   if(fabsf(v)<1e-7f)return false;
   if(sign&&v*sign<0)return false;
   sign=v;}
 }
 return true;
}
bool dp_terminal_accept(const struct lp_space *space,dw_pose pose,float pad,dw_pose preference,float heading_error,const struct lp_config *c){
 return dw_pose_finite(preference)&&isfinite(heading_error)&&heading_error>=0&&
  fabsf(dw_angle(pose.yaw-preference.yaw))+heading_error<1.57079632679489661923f&&dp_goal_contains(space,pose,pad,c);
}
bool dp_goal_seeds(const xenova_drive_ParkingSlot *s,unsigned strategy,const struct lp_config *c,const struct dp_limits *l,
 float pad,struct lp_goal_set *goals,struct lp_space *space){
 if(!s||!c||!goals||!space||!dp_limits_valid(l)||s->corners_count!=4||s->entry_edge>=4||
    (strategy!=1&&strategy!=2)||!isfinite(pad)||pad<0)return false;
 memset(goals,0,sizeof(*goals));*space=(struct lp_space){.parking=true,.slot_id=s->slot_id,.slot_revision=s->geometry_revision};
 memcpy(space->polygon,s->corners,sizeof(space->polygon));
 float radius=hypotf(fmaxf(c->front,c->rear)+c->margin,c->half_width+c->margin);
 space->uncertainty=s->position_bound_m+radius*s->heading_bound_rad;
 dw_point a=s->corners[s->entry_edge],b=s->corners[(s->entry_edge+1)%4];float len=hypotf(b.x-a.x,b.y-a.y);
 if(len<1e-6f)return false;
 float tx=(b.x-a.x)/len,ty=(b.y-a.y)/len,nx=-ty,ny=tx;
 dw_point center={0,0};for(unsigned k=0;k<4;k++){center.x+=s->corners[k].x*.25f;center.y+=s->corners[k].y*.25f;}
 if((center.x-a.x)*nx+(center.y-a.y)*ny<0){nx=-nx;ny=-ny;}
 float heading=atan2f(ny,nx)+(strategy==1?3.14159265358979323846f:0);
 float min_u=FLT_MAX,max_u=-FLT_MAX,min_d=FLT_MAX,max_d=-FLT_MAX;
 for(unsigned k=0;k<4;k++){float u=(s->corners[k].x-a.x)*tx+(s->corners[k].y-a.y)*ty,d=(s->corners[k].x-a.x)*nx+(s->corners[k].y-a.y)*ny;
  min_u=fminf(min_u,u);max_u=fmaxf(max_u,u);min_d=fminf(min_d,d);max_d=fmaxf(max_d,d);}
 float margin=c->margin+space->uncertainty+pad+c->tracking_guard;
 for(unsigned yaw=0;yaw<3;yaw++){
  float angle=heading+(yaw==1?-l->yaw_seed_rad:yaw==2?l->yaw_seed_rad:0),cs=cosf(angle),sn=sinf(angle);
  float lo_u=-FLT_MAX,hi_u=FLT_MAX,lo_d=-FLT_MAX,hi_d=FLT_MAX;
  for(unsigned k=0;k<4;k++){
   float x=k&1?c->front+margin:-c->rear-margin,y=k&2?c->half_width+margin:-c->half_width-margin;
   float dx=cs*x-sn*y,dy=sn*x+cs*y,alpha=dx*tx+dy*ty,beta=dx*nx+dy*ny;
   lo_u=fmaxf(lo_u,min_u-alpha);hi_u=fminf(hi_u,max_u-alpha);lo_d=fmaxf(lo_d,min_d-beta);hi_d=fminf(hi_d,max_d-beta);
  }
  if(lo_u>hi_u||lo_d>hi_d)continue;
  float u[3]={.5f*(lo_u+hi_u),fminf(hi_u,lo_u+l->seed_inward_m),fmaxf(lo_u,hi_u-l->seed_inward_m)};
  float d[3]={.5f*(lo_d+hi_d),fminf(hi_d,lo_d+l->seed_inward_m),fmaxf(lo_d,hi_d-l->seed_inward_m)};
  for(unsigned di=0;di<3;di++)for(unsigned ui=0;ui<3;ui++){
   dw_pose p={a.x+tx*u[ui]+nx*d[di],a.y+ty*u[ui]+ny*d[di],dw_angle(angle)};
   if(!dp_goal_contains(space,p,pad+c->tracking_guard,c))continue;
   bool duplicate=false;for(unsigned k=0;k<goals->count;k++)if(hypotf(p.x-goals->poses[k].x,p.y-goals->poses[k].y)<1e-6f&&fabsf(dw_angle(p.yaw-goals->poses[k].yaw))<1e-6f)duplicate=true;
   if(!duplicate)goals->poses[goals->count++]=p;
  }
 }
 goals->distance=l->goal_distance;goals->heading=l->goal_heading;goals->final_gear=strategy==1?-1:1;return goals->count>0;
}
bool dp_approach(const dw_world *w,const struct tr_intent *task,const xenova_drive_ParkingSlot *s,const struct lp_config *c,dw_pose *out){
 if(!w||!task||!s||!c||!out||s->entry_edge>=4)return false;
 struct tr_view view;if(!tr_open(w,task,&view)||!tr_parking_approach(&view,s->slot_id))return false;
 dw_point a=s->corners[s->entry_edge],b=s->corners[(s->entry_edge+1)%4];dw_pose entry={.5f*(a.x+b.x),.5f*(a.y+b.y),0};
 struct lp_road_state road;if(lp_task_project(w,task,entry,&road)!=LP_PATH_FOUND)return false;
 float horizon=c->wheelbase/tanf(c->max_steer)+c->front+c->tracking_guard;
 return lp_task_goal(w,task,road.pose,horizon,c->front+c->margin+c->tracking_guard,out)==LP_PATH_FOUND;
}
bool dp_rank_better(const struct dp_rank *a,const struct dp_rank *b){
 if(a->clearance_bucket!=b->clearance_bucket)return a->clearance_bucket>b->clearance_bucket;
 if(a->cusps!=b->cusps)return a->cusps<b->cusps;
 if(a->length!=b->length)return a->length<b->length;
 if(a->steering!=b->steering)return a->steering<b->steering;
 return a->slot_id!=b->slot_id?a->slot_id<b->slot_id:a->seed<b->seed;
}
struct dp_rank dp_path_rank(const struct lp_path *p,float clearance,uint32_t slot,unsigned seed,const struct dp_limits *l){
 struct dp_rank r={.slot_id=slot,.seed=seed};
 if(!p||!dp_limits_valid(l))return r;
 r.clearance_bucket=(unsigned)floorf(fminf(fmaxf(0,clearance),l->clearance_sufficient)/l->clearance_band);
 r.cusps=p->execution.segments_count?p->execution.segments_count-1:0;
 for(unsigned k=1;k<p->count;k++){
  r.length+=hypotf(p->points[k].pose.x-p->points[k-1].pose.x,p->points[k].pose.y-p->points[k-1].pose.y);
  r.steering+=fabsf(p->points[k].steering_rad-p->points[k-1].steering_rad);
 }
 return r;
}
