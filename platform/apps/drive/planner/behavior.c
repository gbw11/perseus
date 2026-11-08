/* SPDX-License-Identifier: Apache-2.0 */
/* Episode progress uses original station/goal and Pi absolute travel, not paths. */
#include "behavior.h"
#include "lattice.h"
#include <float.h>
#include <math.h>
#include <string.h>

bool db_limits_valid(const struct db_limits *l){
 return l&&l->episode_us&&l->episode_us<=40000000&&l->phase_us&&l->phase_us<=10000000&&
  l->progress_us&&l->progress_us<=2000000&&l->settle_us&&l->settle_us<=l->phase_us&&
  isfinite(l->travel_m)&&l->travel_m>0&&l->travel_m<=6&&isfinite(l->progress_m)&&l->progress_m>0&&
  isfinite(l->pass_margin_m)&&l->pass_margin_m>=0&&isfinite(l->heading_rad)&&l->heading_rad>0&&
  isfinite(l->steering_rad)&&l->steering_rad>0&&(l->policy==DB_RETURN_HOME||l->policy==DB_KEEP_TARGET);
}
static bool station(const struct tr_basis *b,dw_point p,float bound,float *value){
 if(!b||!b->bound||b->count<2)return false;
 float best=FLT_MAX,selected=0,arc=0;bool ambiguous=false;
 for(unsigned i=1;i<b->count;i++){
  dw_point a=b->points[i-1],z=b->points[i];float dx=z.x-a.x,dy=z.y-a.y,len=hypotf(dx,dy);
  if(!isfinite(len)||len<1e-6f)return false;
  float t=fmaxf(0,fminf(1,((p.x-a.x)*dx+(p.y-a.y)*dy)/(len*len)));
  float d=hypotf(p.x-a.x-t*dx,p.y-a.y-t*dy),s=arc+t*len;
  if(d+bound<best){best=d;selected=s;ambiguous=false;}
  else if(fabsf(d-best)<=bound&&fabsf(s-selected)>2*bound+1e-4f)ambiguous=true;
  arc+=len;
 }
 if(ambiguous||!isfinite(best))return false;
 *value=selected;return true;
}
bool db_station_bounds(const struct tr_basis *b,dw_pose pose,float pad,const struct lp_config *c,float *lo,float *hi){
 if(!c||!lo||!hi||!dw_pose_finite(pose)||!isfinite(pad)||pad<0)return false;
 *lo=FLT_MAX;*hi=-FLT_MAX;
 float cs=cosf(pose.yaw),sn=sinf(pose.yaw);
 for(unsigned i=0;i<4;i++){
  float x=(i&1?c->front:-c->rear),y=(i&2?c->half_width:-c->half_width),s;
  dw_point p={pose.x+cs*x-sn*y,pose.y+sn*x+cs*y};
  if(!station(b,p,pad,&s))return false;
  *lo=fminf(*lo,s-pad-c->margin);*hi=fmaxf(*hi,s+pad+c->margin);
 }
 return true;
}
bool db_station_point(const struct tr_basis *b,dw_point p,float pad,float *out){return station(b,p,pad,out);}
bool db_blocker_observe(const dw_world *w,const struct tr_intent *task,dw_pose pose,float horizon,
 const struct lp_config *c,struct db_blocker *out){
 if(!w||!task||!c||!out||!isfinite(horizon)||horizon<=0||!w->has_drivable_permission||
    w->drivable_permission.size<((size_t)w->width*w->height+7)/8)return false;
 memset(out,0,sizeof(*out));out->lower=(dw_point){FLT_MAX,FLT_MAX};out->upper=(dw_point){-FLT_MAX,-FLT_MAX};
 out->station_lower=FLT_MAX;out->station_upper=-FLT_MAX;
 struct lp_road_state start;if(lp_task_project(w,task,pose,&start)!=LP_PATH_FOUND)return false;
 float cell=w->resolution_m,half=cell*.707106782f;
 for(unsigned y=0;y<w->height;y++)for(unsigned x=0;x<w->width;x++){
  size_t at=(size_t)y*w->width+x;unsigned occ=w->occupancy.bytes[at],flags=w->cell_flags.bytes[at];
  bool occupied=occ>=70&&occ<=100&&(flags&1)&&!(flags&4);
  bool denied=!(flags&1)||(flags&4)||occ==255||
   !(w->drivable_permission.bytes[at/8]&(1u<<(at%8)));
  if(!occupied&&!denied)continue;
  dw_pose p=dw_compose(w->origin,(dw_pose){(x+.5f)*cell,(y+.5f)*cell,0});struct lp_road_state road;
  if(lp_task_project(w,task,p,&road)!=LP_PATH_FOUND)return false;
  if(road.arc+half<start.arc-c->rear||road.arc-half>start.arc+horizon||
     fabsf(road.offset)>c->half_width+c->margin+c->tracking_guard+task->vehicle_pad+half)continue;
  if(!occupied){out->unknown=true;continue;}
  out->present=true;out->lower.x=fminf(out->lower.x,p.x-half);out->lower.y=fminf(out->lower.y,p.y-half);
  out->upper.x=fmaxf(out->upper.x,p.x+half);out->upper.y=fmaxf(out->upper.y,p.y+half);
  out->station_lower=fminf(out->station_lower,road.arc-half);out->station_upper=fmaxf(out->station_upper,road.arc+half);
 }
 if(out->present){strcpy(out->map,w->map_epoch);out->observation_sequence=w->observation_seq;}
 return true;
}
static bool facts_current(const struct db_facts *f,uint64_t now){
 return f&&f->valid&&f->meter_valid&&f->vehicle_sequence&&now<f->expires_pi_us&&
  isfinite(f->metres_per_tick)&&f->metres_per_tick>0&&
  f->pi_session[0]&&f->device_session[0]&&f->odom_epoch[0];
}
void db_episode_hold(struct db_episode *e,enum db_reason why){
 if(e){e->phase=DB_HOLD;e->reason=why;e->terminal=true;e->waiting_stop=false;}
}
bool db_episode_phase(struct db_episode *e,enum db_phase phase,dw_pose goal,uint64_t now,const struct db_limits *l){
 if(!e||!e->active||e->terminal||!db_limits_valid(l)||!dw_pose_finite(goal)||now<e->started_pi_us||
    now>=e->deadline_pi_us||e->phase_revision==UINT64_MAX)return false;
 if(e->phase==phase)return true;
 e->phase=phase;e->phase_revision++;e->phase_entered_pi_us=now;
 uint64_t cap=now>UINT64_MAX-l->phase_us?UINT64_MAX:now+l->phase_us;
 e->phase_deadline_pi_us=cap<e->deadline_pi_us?cap:e->deadline_pi_us;e->phase_goal=goal;
 e->last_progress_pi_us=0;e->waited_us=e->wait_started_pi_us=0;e->waiting_stop=false;e->settle_since_pi_us=0;
 e->progress_lower=-FLT_MAX;e->remaining_upper=FLT_MAX;return true;
}
bool db_episode_begin(struct db_episode *e,const uint8_t id[16],uint32_t home,uint32_t target,
 const struct tr_basis *basis,const struct db_blocker *blocker,dw_pose goal,uint64_t now,
 const struct db_facts *f,const struct db_limits *l){
 if(!e||e->active||!id||!home||!target||home==target||!basis||!basis->bound||!blocker||!blocker->present||
    blocker->unknown||!facts_current(f,now)||!db_limits_valid(l)||now>INT64_MAX-l->episode_us)return false;
 memset(e,0,sizeof(*e));e->active=true;memcpy(e->id,id,16);e->revision=1;e->home_lane_id=home;e->target_lane_id=target;
 e->policy=l->policy;e->basis=*basis;e->blocker=*blocker;e->started_pi_us=now;e->deadline_pi_us=now+l->episode_us;
 e->meter_start_ticks=f->meter_ticks;e->meter_revision=f->meter_revision;e->metres_per_tick=f->metres_per_tick;
 memcpy(e->meter_epoch,f->meter_epoch,16);
 strcpy(e->pi_session,f->pi_session);strcpy(e->device_session,f->device_session);strcpy(e->odom_epoch,f->odom_epoch);
 return db_episode_phase(e,DB_ASSESS,goal,now,l);
}
double db_travel_upper(const struct db_facts *f,uint64_t baseline,uint64_t now,float speed){
 if(!f||!f->meter_valid||!f->sampled_pi_us||now<f->sampled_pi_us||!isfinite(speed)||speed<0||
    !isfinite(f->metres_per_tick)||f->metres_per_tick<=0||f->meter_ticks<baseline||
    f->meter_ticks-baseline>UINT64_C(9007199254740992))return INFINITY;
 double age=(double)(now-f->sampled_pi_us)/1000000.+(double)f->clock_error_us/1000000.;
 double measured=nextafter((double)(f->meter_ticks-baseline)*f->metres_per_tick,INFINITY);
 return nextafter(measured+nextafter(age*speed,INFINITY),INFINITY);
}
enum db_reason db_episode_step(struct db_episode *e,const dw_world *w,dw_pose pose,const dw_vehicle *vehicle,
 uint64_t now,const struct db_facts *f,const struct db_limits *l,const struct lp_config *c,bool wait){
 if(!e||!e->active)return DB_OK;
 if(e->terminal)return e->reason;
 enum db_reason why=DB_OK;
 if(!db_limits_valid(l)||!w||!vehicle||!c||!tr_basis_compatible(&e->basis,w)||
    strcmp(e->blocker.map,w->map_epoch)||strcmp(e->odom_epoch,vehicle->odom_epoch))why=DB_CONTEXT;
 else if(!facts_current(f,now)||strcmp(e->pi_session,f->pi_session)||strcmp(e->device_session,f->device_session)||
    strcmp(e->odom_epoch,f->odom_epoch)||memcmp(e->meter_epoch,f->meter_epoch,16)||f->meter_revision!=e->meter_revision||f->metres_per_tick!=e->metres_per_tick||
    f->meter_ticks<e->meter_start_ticks||f->meter_ticks-e->meter_start_ticks<e->meter_used_ticks||
    f->meter_ticks-e->meter_start_ticks>UINT64_C(9007199254740992))why=DB_METER;
 else if(now<e->phase_entered_pi_us||now>=e->deadline_pi_us||now>=e->phase_deadline_pi_us)why=DB_TIME;
 else if(db_travel_upper(f,e->meter_start_ticks,now,fmaxf(c->max_velocity,c->max_reverse_velocity))>=l->travel_m)why=DB_TRAVEL;
 if(why){db_episode_hold(e,why);return why;}
 e->meter_used_ticks=f->meter_ticks-e->meter_start_ticks;
 /* Only an explicit normal STOP phase suspends the movement stopwatch. */
 if(wait){if(!e->waiting_stop){e->waiting_stop=true;e->wait_started_pi_us=now;}return DB_OK;}
 if(e->waiting_stop){e->waited_us+=now-e->wait_started_pi_us;e->waiting_stop=false;}
 float radius=hypotf(fmaxf(c->front,c->rear)+c->margin,c->half_width+c->margin);
 float pad=vehicle->position_error_m+radius*vehicle->heading_error_rad,lo,hi;
 if(!db_station_bounds(&e->basis,pose,pad,c,&lo,&hi)){db_episode_hold(e,DB_GEOMETRY);return DB_GEOMETRY;}
 float remaining=hypotf(pose.x-e->phase_goal.x,pose.y-e->phase_goal.y)+pad;
 bool fresh=vehicle->sequence>e->last_vehicle_sequence;
 if(e->progress_lower==-FLT_MAX){e->progress_lower=lo;e->remaining_upper=remaining;}
 else if(fresh&&(lo>=e->progress_lower+l->progress_m||remaining+l->progress_m<=e->remaining_upper)){
  e->progress_lower=fmaxf(e->progress_lower,lo);e->remaining_upper=fminf(e->remaining_upper,remaining);
  e->last_progress_pi_us=now;e->waited_us=0;
 }
 e->last_vehicle_sequence=vehicle->sequence;
 uint64_t anchor=e->last_progress_pi_us?e->last_progress_pi_us:e->phase_entered_pi_us;
 if(now-anchor>=e->waited_us+l->progress_us){db_episode_hold(e,DB_NO_PROGRESS);return DB_NO_PROGRESS;}
 (void)hi;return DB_OK;
}
