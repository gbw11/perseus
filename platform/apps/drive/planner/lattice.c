/* SPDX-License-Identifier: Apache-2.0 */
#include "lattice.h"
#include <math.h>
#include <string.h>
#include <float.h>

#define PI 3.14159265358979323846f
_Static_assert(sizeof(struct lp_node)<=64,"signed gear/cusp labels preserve the design node bound");
static float clamp(float v,float low,float high) { return fminf(high,fmaxf(low,v)); }
static enum lp_result guard(const struct lp_budget *b,struct lp_metrics *m) {
 uint64_t now;
 m->cancel_checks++;
 if(b->cancelled&&b->cancelled(b->context))return LP_CANCELLED;
 m->clock_checks++;
 if(!b->clock||b->clock(b->context,&now))return LP_CLOCK;
 return now>=b->deadline_us?LP_DEADLINE:LP_SEARCHING;
}
static float distance(dw_pose a,dw_pose b) { return hypotf(a.x-b.x,a.y-b.y); }
static dw_point boundary(const dw_world *w,unsigned i) {
 return i<w->left_boundary_count?w->left_boundary[i]:w->right_boundary[2*w->left_boundary_count-1-i];
}
static bool inside_road(const dw_world *w,float x,float y,struct lp_metrics *m) {
 bool inside=false;unsigned n=2*w->left_boundary_count;
 for(unsigned i=0,j=n-1;i<n;j=i++) {
  dw_point a=boundary(w,i),b=boundary(w,j);m->road_checks++;
  if(((a.y>y)!=(b.y>y))&&(x<(b.x-a.x)*(y-a.y)/(b.y-a.y)+a.x))inside=!inside;
 }
 return inside;
}
static bool road_edge_cuts_box(dw_point a,dw_point b,float x,float y,float cs,float sn,float half,float width) {
 float ax=(a.x-x)*cs+(a.y-y)*sn,ay=-(a.x-x)*sn+(a.y-y)*cs;
 float bx=(b.x-x)*cs+(b.y-y)*sn,by=-(b.x-x)*sn+(b.y-y)*cs;
 float lo=0,hi=1,first[2]={ax,ay},delta[2]={bx-ax,by-ay},bounds[2]={half,width};
 for(unsigned k=0;k<2;k++) {
  if(fabsf(delta[k])<1e-9f){if(fabsf(first[k])>=bounds[k])return false;}
  else {float t0=(-bounds[k]-first[k])/delta[k],t1=(bounds[k]-first[k])/delta[k];
   if(t0>t1){float swap=t0;t0=t1;t1=swap;}lo=fmaxf(lo,t0);hi=fminf(hi,t1);if(lo>hi)return false;}
 }
 return lo<=hi;
}
static bool blocked(const dw_world *w,size_t i,const struct lp_config *c) {
 unsigned flags=w->cell_flags.bytes[i];const uint8_t *p=w->age_le32.bytes+4*i;
 uint32_t age=(uint32_t)p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;
 bool denied=(w->required_features&DW_LANE_MODEL)&&
  (!w->has_drivable_permission||i/8>=w->drivable_permission.size||!(w->drivable_permission.bytes[i/8]&(1u<<(i%8))));
 return denied||!(flags&1)||(flags&4)||(!(flags&2)&&(double)age+c->live_age_us/1000>c->cell_age_ms)||w->occupancy.bytes[i]>=70;
}
void lp_state_import(struct lp_path *path,const dw_state *state){
 path->execution=(struct lp_execution){.present=true,.features=state->state_features,.mission=state->mission,
  .segments_count=state->segments_count,.has_direction_gate=state->has_direction_gate,.direction_gate=state->direction_gate};
 memcpy(path->execution.segments,state->segments,state->segments_count*sizeof(state->segments[0]));
 memset(&path->task,0,sizeof(path->task));memset(&path->basis,0,sizeof(path->basis));
 path->initial_tracker_index=state->tracker_index;path->initial_pi_us=0;
 if(state->has_task_reference){
  const xenova_drive_TaskReferenceState *in=&state->task_reference;
  path->task=(struct tr_intent){true,in->changing,in->selected_lane_id,in->from_lane_id,in->home_lane_id,0};
  struct tr_basis *basis=&path->basis;basis->bound=true;basis->count=in->basis_prefix_count;basis->revision=in->basis_revision;
  memcpy(basis->points,in->basis_prefix,basis->count*sizeof(basis->points[0]));
  strcpy(basis->id,state->reference_id);strcpy(basis->map,state->map_epoch);strcpy(basis->frame,state->frame_epoch);
  strcpy(basis->odom,state->odom_epoch);strcpy(basis->configuration,state->configuration);strcpy(basis->calibration,state->calibration);
  strcpy(basis->primitive,state->primitive);strcpy(basis->vehicle,in->vehicle);strcpy(basis->mapper_boot,in->mapper_boot);
  strcpy(basis->mapper_instance,in->mapper_instance);strcpy(basis->clock_epoch,in->clock_epoch);
 }
}
bool lp_state_export(const struct lp_path *path,dw_state *state){
 if(!path||!state||path->task.active!=path->basis.bound)return false;
 state->major=2;state->has_state_features=true;state->state_features=DW_STATE_FEATURES;state->has_mission=true;
 state->has_task_reference=path->task.active;
 if(path->task.active){
  const struct tr_basis *basis=&path->basis;xenova_drive_TaskReferenceState *out=&state->task_reference;
  if(basis->count<2||basis->count>64||strcmp(basis->id,path->reference)||strcmp(basis->frame,path->frame)||
     strcmp(basis->odom,path->odom)||strcmp(basis->configuration,path->configuration))return false;
  memset(out,0,sizeof(*out));out->home_lane_id=path->task.home_lane_id;out->from_lane_id=path->task.from_lane_id;
  out->selected_lane_id=path->task.selected_lane_id;out->changing=path->task.changing;out->basis_revision=basis->revision;
  out->basis_prefix_count=basis->count;memcpy(out->basis_prefix,basis->points,basis->count*sizeof(basis->points[0]));
  strcpy(out->vehicle,basis->vehicle);strcpy(out->mapper_boot,basis->mapper_boot);
  strcpy(out->mapper_instance,basis->mapper_instance);strcpy(out->clock_epoch,basis->clock_epoch);
  state->state_features|=DW_TASK_REFERENCE;
 }
 if(path->execution.present){
  if(path->execution.features!=state->state_features)return false;
  state->mission=path->execution.mission;
  state->segments_count=path->execution.segments_count;
  memcpy(state->segments,path->execution.segments,state->segments_count*sizeof(state->segments[0]));
  state->has_direction_gate=path->execution.has_direction_gate;state->direction_gate=path->execution.direction_gate;
 }else{
  /* 순수 planner의 미승인 후보. LANE 임무나 권위를 독자 생성하지 않는다. */
  memset(&state->mission,0,sizeof(state->mission));
  state->mission.mission_id.size=state->mission.operator_request_nonce.size=state->mission.scene_admission_id.size=16;
  state->segments_count=0;state->has_direction_gate=false;
  bool moving=false;for(size_t i=0;i<path->count;i++)moving|=path->points[i].velocity_mps!=0;
  if(path->execution.segments_count){state->segments_count=path->execution.segments_count;
   memcpy(state->segments,path->execution.segments,state->segments_count*sizeof(state->segments[0]));}
  else if(moving&&path->count>=2){state->segments_count=1;state->segments[0]=(xenova_drive_GearSegment){0,(uint32_t)path->count-1,1};}
 }
 return true;
}
bool lp_mission_matches(const struct lp_path *path,const xenova_drive_MissionGrant *g){
 const struct lp_execution *e=&path->execution;const xenova_drive_MissionExecution *m=&e->mission;
 uint32_t expected=DW_STATE_FEATURES|(path->task.active?DW_TASK_REFERENCE:0);
 return e->present&&e->features==expected&&g&&g->admitted&&g->kind==1&&m->kind==1&&m->phase==10&&
  m->mission_revision==g->mission_revision&&m->grant_revision==g->grant_revision&&m->cancel_fence_revision==g->cancel_fence_revision&&
  !memcmp(m->mission_id.bytes,g->mission_id.bytes,16)&&!memcmp(m->scene_admission_id.bytes,g->scene_admission_id.bytes,16)&&
  m->scene_admission_revision==g->scene_revision;
}
void lp_index_build(struct lp_collision_index *index,const dw_world *w,const struct lp_config *c) {
 memset(index,0,sizeof(*index));index->world=w;index->sequence=w->sequence;
 index->cell_age_ms=c->cell_age_ms;index->live_age_us=c->live_age_us;
 for(unsigned y=0;y<w->height;y++)for(unsigned x=0;x<w->width;x++)
  {
   size_t at=(size_t)y*w->width+x;
   if(blocked(w,at,c))index->rows[y]|=UINT64_C(1)<<x;
   if((w->required_features&DW_ROAD_PRIORITY)&&!(w->cell_flags.bytes[at]&DW_CELL_ROAD))
    index->shoulder[y]|=UINT64_C(1)<<x;
  }
}
static enum lp_result footprint(const dw_world *w,dw_pose pose,float extra,const struct lp_config *c,
 struct lp_metrics *m,const struct lp_budget *b,bool obstacle_evidence,bool *outside,const struct tr_intent *task) {
 if(outside)*outside=false;
 enum lp_result rc=guard(b,m);if(rc!=LP_SEARCHING)return rc;
 if(!dw_pose_finite(pose)||!isfinite(extra)||extra<0)return LP_NUMERIC;
 if(m->cells>=c->collision_limit)return LP_WORK_BUDGET;
 m->footprints++;
 if(!obstacle_evidence&&task&&task->active){struct tr_view view;
  extra+=task->vehicle_pad;
  if(!isfinite(extra)||!tr_open(w,task,&view)||!tr_contains(&view,pose,extra,c))return LP_COLLISION;
 }
 float half=(c->front+c->rear)*.5f+c->margin+extra,width=c->half_width+c->margin+extra;
 float cs=cosf(pose.yaw),sn=sinf(pose.yaw),shift=(c->front-c->rear)*.5f;
 float center_x=pose.x+shift*cs,center_y=pose.y+shift*sn;
 if(!obstacle_evidence){
 for(unsigned i=0;i<4;i++) {
  float u=(i&1)?half:-half,v=(i&2)?width:-width;
  if(!inside_road(w,center_x+cs*u-sn*v,center_y+sn*u+cs*v,m))return LP_COLLISION;
 }
 unsigned vertices=2*w->left_boundary_count;
 for(unsigned k=0;k<vertices;k++) {
  m->road_checks++;
  if(!(k&31)){rc=guard(b,m);if(rc!=LP_SEARCHING)return rc;}
  if(road_edge_cuts_box(boundary(w,k),boundary(w,(k+1)%vertices),center_x,center_y,cs,sn,half,width))return LP_COLLISION;
 }
 }
 dw_pose local=dw_compose(dw_inverse(w->origin),pose);
 cs=cosf(local.yaw);sn=sinf(local.yaw);
 center_x=local.x+shift*cs;center_y=local.y+shift*sn;
 float ex=fabsf(cs)*half+fabsf(sn)*width,ey=fabsf(sn)*half+fabsf(cs)*width;
 if(!isfinite(center_x)||!isfinite(center_y)||!isfinite(ex)||!isfinite(ey)||
    center_x-ex<0||center_y-ey<0||center_x+ex>=w->width*w->resolution_m||
    center_y+ey>=w->height*w->resolution_m)return obstacle_evidence?LP_PATH_FOUND:LP_COLLISION;
 int x0=(int)floorf((center_x-ex)/w->resolution_m),x1=(int)floorf((center_x+ex)/w->resolution_m);
 int y0=(int)floorf((center_y-ey)/w->resolution_m),y1=(int)floorf((center_y+ey)/w->resolution_m);
 if(x0<0||y0<0||x1>=(int)w->width||y1>=(int)w->height)return LP_COLLISION;
 float cell=w->resolution_m*.5f;
 const struct lp_collision_index *index=b->collision_index;
 bool indexed=index&&index->world==w&&index->sequence==w->sequence&&
              index->cell_age_ms==c->cell_age_ms&&index->live_age_us==c->live_age_us;
 for(int y=y0;y<=y1;y++) {
  if(++m->cells>c->collision_limit)return LP_WORK_BUDGET;
  uint64_t candidates=0,hard=0;
  if(indexed) {
   uint64_t lower=~UINT64_C(0)<<x0,upper=x1==63?~UINT64_C(0):(UINT64_C(1)<<(x1+1))-1;
   hard=index->rows[y]&lower&upper;candidates=hard;
   if(outside&&!*outside)candidates|=index->shoulder[y]&lower&upper;
  }else for(int x=x0;x<=x1;x++) {
   size_t at=(size_t)y*w->width+x;uint64_t bit=UINT64_C(1)<<x;
   if(blocked(w,at,c))hard|=bit;
   if(outside&&!*outside&&(w->required_features&DW_ROAD_PRIORITY)&&!(w->cell_flags.bytes[at]&DW_CELL_ROAD))candidates|=bit;
  }
  candidates|=hard;
  while(candidates) {
   int x=__builtin_ctzll(candidates);candidates&=candidates-1;
   if(++m->cells>c->collision_limit)return LP_WORK_BUDGET;
   if(!(m->cells&31)){rc=guard(b,m);if(rc!=LP_SEARCHING)return rc;}
  float dx=(x+.5f)*w->resolution_m-center_x,dy=(y+.5f)*w->resolution_m-center_y;
  /* OBB/cell SAT: grid axes already overlap by the bounding range. The two
   * body axes complete the test, including obstacle cells at body corners. */
  if(fabsf(dx*cs+dy*sn)<=half+cell*(fabsf(cs)+fabsf(sn))&&
     fabsf(-dx*sn+dy*cs)<=width+cell*(fabsf(cs)+fabsf(sn))) {
   if(hard&(UINT64_C(1)<<x))return LP_COLLISION;
   *outside=true;candidates&=hard;
  }
  }
 }
 return LP_PATH_FOUND;
}

enum lp_result lp_footprint(const dw_world *w,dw_pose pose,float extra,const struct lp_config *c,
 struct lp_metrics *m,const struct lp_budget *b) {
 return footprint(w,pose,extra,c,m,b,false,NULL,NULL);
}

static bool reference(const dw_world *w,const struct tr_intent *task,const dw_point **points,unsigned *count){
 if(task&&task->active){struct tr_view view;if(!tr_open(w,task,&view))return false;*points=view.points;*count=view.count;}
 else{*points=w->reference;*count=w->reference_count;}
 return *count>=2;
}
static float reference_offset(const dw_world *w,const struct tr_intent *task,dw_pose p) {
 const dw_point *points;unsigned count;if(!reference(w,task,&points,&count))return NAN;
 float best=FLT_MAX,offset=0;
 for(unsigned i=1;i<count;i++) {
  dw_point a=points[i-1],b=points[i];
  float dx=b.x-a.x,dy=b.y-a.y,l2=dx*dx+dy*dy;
  if(l2<1e-10f)continue;
  float t=clamp(((p.x-a.x)*dx+(p.y-a.y)*dy)/l2,0,1);
  float ex=p.x-a.x-t*dx,ey=p.y-a.y-t*dy,d=ex*ex+ey*ey;
  if(d<best){best=d;offset=(dx*ey-dy*ex)/sqrtf(l2);}
 }
 return offset;
}

enum lp_result lp_reference_project(const dw_world *w,dw_pose pose,struct lp_road_state *out) {
 return lp_task_project(w,NULL,pose,out);
}
enum lp_result lp_task_project(const dw_world *w,const struct tr_intent *task,dw_pose pose,struct lp_road_state *out) {
 const dw_point *points;unsigned count;
 if(!w||!out||!reference(w,task,&points,&count)||!dw_pose_finite(pose))return LP_INPUT;
 memset(out,0,sizeof(*out));float best=FLT_MAX;unsigned center=1;
 for(unsigned i=1;i<count;i++) {
  dw_point a=points[i-1],b=points[i];
  float dx=b.x-a.x,dy=b.y-a.y,l2=dx*dx+dy*dy;
  if(!isfinite(l2)||l2<1e-10f)return LP_INPUT;
  float len=sqrtf(l2),t=clamp(((pose.x-a.x)*dx+(pose.y-a.y)*dy)/l2,0,1);
  float ex=pose.x-a.x-t*dx,ey=pose.y-a.y-t*dy,d=hypotf(ex,ey);
  if(d<best){
   best=d;center=t<.5f?i-1:i;out->arc=out->length+t*len;
   out->pose=(dw_pose){a.x+t*dx,a.y+t*dy,atan2f(dy,dx)};out->offset=(dx*ey-dy*ex)/len;
  }
  out->length+=len;
 }
 if(!isfinite(out->length))return LP_NUMERIC;
 if(count<3)return LP_PATH_FOUND;
 if(!center)center=1;
 if(center+1>=count)center=count-2;
 dw_point a=points[center-1],b=points[center],d=points[center+1];
 float ab=hypotf(b.x-a.x,b.y-a.y),bd=hypotf(d.x-b.x,d.y-b.y),ad=hypotf(d.x-a.x,d.y-a.y);
 if(ab<1e-6f||bd<1e-6f||ad<1e-6f)return LP_INPUT;
 out->curvature=2*((b.x-a.x)*(d.y-b.y)-(b.y-a.y)*(d.x-b.x))/(ab*bd*ad);
 return isfinite(out->curvature)?LP_PATH_FOUND:LP_NUMERIC;
}
static bool grid_segment(dw_pose a,dw_pose b,float width,float height,float reserve,float *enter,float *leave) {
 float low=0,high=1,first[2]={a.x,a.y},delta[2]={b.x-a.x,b.y-a.y},limit[2]={width,height};
 for(unsigned k=0;k<2;k++) {
  if(fabsf(delta[k])<1e-9f){if(first[k]<reserve||first[k]>limit[k]-reserve)return false;}
  else {
   float p=(reserve-first[k])/delta[k],q=(limit[k]-reserve-first[k])/delta[k];
   if(p>q){float tmp=p;p=q;q=tmp;}low=fmaxf(low,p);high=fminf(high,q);if(low>high)return false;
  }
 }
 *enter=low;*leave=high;return true;
}
enum lp_result lp_reference_goal(const dw_world *w,dw_pose pose,float horizon,float end_clearance,dw_pose *out) {
 return lp_task_goal(w,NULL,pose,horizon,end_clearance,out);
}
enum lp_result lp_task_goal(const dw_world *w,const struct tr_intent *task,dw_pose pose,float horizon,float end_clearance,dw_pose *out) {
 const dw_point *points;unsigned count;
 if(!w||!out||!reference(w,task,&points,&count)||!dw_pose_finite(pose)||!isfinite(horizon)||horizon<=0||!isfinite(end_clearance)||end_clearance<0)return LP_INPUT;
 struct lp_road_state road;enum lp_result rc=lp_task_project(w,task,pose,&road);if(rc!=LP_PATH_FOUND)return rc;
 if(road.length<=end_clearance)return LP_INPUT;
 float goal=fminf(road.length-end_clearance,road.arc+horizon),total=0;
 float width=w->width*w->resolution_m,height=w->height*w->resolution_m;
 if(goal<=road.arc||2*end_clearance>=fminf(width,height))return LP_INPUT;
 dw_pose inverse=dw_inverse(w->origin);bool entered=false;
 for(unsigned i=1;i<count;i++) {
  dw_point a=points[i-1],b=points[i];float dx=b.x-a.x,dy=b.y-a.y,len=hypotf(dx,dy);
  float lo=fmaxf(0,(road.arc-total)/len),hi=fminf(1,(goal-total)/len);
  if(hi>lo+1e-6f&&lo<=1&&hi>=0) {
   dw_pose first={a.x+lo*dx,a.y+lo*dy,0},last={a.x+hi*dx,a.y+hi*dy,0};
   dw_pose local_first=dw_compose(inverse,first),local_last=dw_compose(inverse,last);
   float begin,end;
   bool visible=grid_segment(local_first,local_last,width,height,end_clearance,&begin,&end);
   if(visible) {
    float t=lo+(hi-lo)*end;
    if(total+t*len<=road.arc+1e-4f)return LP_INPUT;
    *out=(dw_pose){a.x+t*dx,a.y+t*dy,atan2f(dy,dx)};entered=true;
    if(end<1||total+len>=goal)return LP_PATH_FOUND;
   }else if(entered)return LP_PATH_FOUND;
   /* A near-rear start may enter the reserved box shortly ahead. Never skip
    * an intervening exit from the actual grid to select a later re-entry. */
   if(local_first.x<0||local_first.y<0||local_first.x>=width||local_first.y>=height||
      local_last.x<0||local_last.y<0||local_last.x>=width||local_last.y>=height)return LP_INPUT;
  }
  total+=len;
 }
 return entered?LP_PATH_FOUND:LP_INPUT;
}
static void integrate(dw_pose *pose,float steer,float velocity,float dt,float wheelbase) {
 float turn=velocity/wheelbase*tanf(steer)*dt,ds=velocity*dt;
 if(fabsf(turn)<1e-7f){pose->x+=ds*cosf(pose->yaw);pose->y+=ds*sinf(pose->yaw);}
 else {float radius=ds/turn;pose->x+=radius*(sinf(pose->yaw+turn)-sinf(pose->yaw));
       pose->y-=radius*(cosf(pose->yaw+turn)-cosf(pose->yaw));}
 pose->yaw=dw_angle(pose->yaw+turn);
}
static void quantize(struct lp_search *s,struct lp_node *n) {
 dw_pose p=dw_compose(dw_inverse(s->world->origin),n->pose);
 n->qx=(int16_t)floorf(p.x/s->config->resolution);
 n->qy=(int16_t)floorf(p.y/s->config->resolution);
 int angle=(int)lroundf(dw_angle(p.yaw)*(32/(2*PI)));
 n->heading=(uint8_t)((angle+32)%32);
}
static unsigned hash(const struct lp_node *n) {
 return ((uint32_t)(uint16_t)n->qx*73856093u^(uint32_t)(uint16_t)n->qy*19349663u^
         (unsigned)n->heading*83492791u^(unsigned)n->steering*2654435761u^
         (unsigned)(n->gear+1)*2246822519u^(unsigned)n->cusps*3266489917u)&(LP_INDEX-1);
}
static bool key_equal(const struct lp_node *a,const struct lp_node *b) {
 return a->qx==b->qx&&a->qy==b->qy&&a->heading==b->heading&&a->steering==b->steering&&
  a->gear==b->gear&&a->cusps==b->cusps;
}
static int index_slot(struct lp_search *s,const struct lp_node *n,bool *exists) {
 unsigned first=hash(n);
 for(unsigned k=0;k<32;k++) {
  unsigned slot=(first+k)&(LP_INDEX-1);int found=s->index[slot];
  if(k+1>s->metrics.index_probes_max)s->metrics.index_probes_max=k+1;
  if(found<0){*exists=false;return (int)slot;}
  if(key_equal(&s->nodes[found],n)){*exists=true;return (int)slot;}
 }
 return -1;
}
static void heap_swap(struct lp_search *s,unsigned a,unsigned b) {
 int tmp=s->heap[a];s->heap[a]=s->heap[b];s->heap[b]=tmp;
 s->nodes[s->heap[a]].heap=(int)a;s->nodes[s->heap[b]].heap=(int)b;
}
static float road_priority(const struct lp_search *s,const struct lp_node *n) {
 if(!(s->world->required_features&DW_ROAD_PRIORITY))return 0;
 float value=n->road_cost;
 if(!s->building_heuristic&&n->qx>=0&&n->qy>=0&&n->qx<(int)s->world->width&&n->qy<(int)s->world->height) {
  float remaining=s->heuristic[n->qy*s->world->width+n->qx];
  if(remaining<FLT_MAX)value+=remaining;
 }
 return value;
}
static bool less(const struct lp_search *s,unsigned a,unsigned b) {
 const struct lp_node *x=&s->nodes[s->heap[a]],*y=&s->nodes[s->heap[b]];
 float rx=road_priority(s,x),ry=road_priority(s,y);
 if(rx!=ry)return rx<ry;
 return x->f<y->f||(x->f==y->f&&x->g>y->g);
}
static void up(struct lp_search *s,unsigned at) { while(at&&less(s,at,(at-1)/2)){unsigned next=(at-1)/2;heap_swap(s,at,next);at=next;} }
static void push(struct lp_search *s,int n) {
 s->heap[s->open]=n;s->nodes[n].heap=(int)s->open;up(s,s->open++);
 if(s->open>s->metrics.open_high_water)s->metrics.open_high_water=s->open;
}
static int pop(struct lp_search *s) {
 int n=s->heap[0];s->nodes[n].heap=-1;s->open--;
 if(s->open){s->heap[0]=s->heap[s->open];s->nodes[s->heap[0]].heap=0;unsigned at=0;
  for(;;){unsigned left=2*at+1;if(left>=s->open)break;unsigned child=left;
   if(left+1<s->open&&less(s,left+1,left))child++;
   if(!less(s,child,at))break;
   heap_swap(s,at,child);at=child;
  }
 }
 return n;
}
static float heuristic(const struct lp_search *s,dw_pose p) {
 /* Weighted, guidance-only heuristic. Quantized pruning and a finite control
  * set deliberately carry no optimality or completeness claim. */
 dw_pose local=dw_compose(dw_inverse(s->world->origin),p);
 int x=(int)floorf(local.x/s->world->resolution_m),y=(int)floorf(local.y/s->world->resolution_m);
 float value=distance(p,s->goal);
 if(!(s->world->required_features&DW_ROAD_PRIORITY)&&x>=0&&y>=0&&x<(int)s->world->width&&y<(int)s->world->height) {
  float obstacle=s->heuristic[y*s->world->width+x];if(obstacle<FLT_MAX)value=fmaxf(value,obstacle);
 }
 return s->config->heuristic_weight*value+s->config->heading_weight*fabsf(dw_angle(p.yaw-s->goal.yaw));
}
static bool heuristic_blocked(const struct lp_search *s,int x,int y) {
 const dw_world *w=s->world;
 if(x<0||y<0||x>=(int)w->width||y>=(int)w->height)return true;
 int radius=(int)ceilf((s->config->half_width+s->config->margin+s->config->tracking_guard)/w->resolution_m);
 for(int dy=-radius;dy<=radius;dy++)for(int dx=-radius;dx<=radius;dx++) {
  if(dx*dx+dy*dy>radius*radius)continue;
  int xx=x+dx,yy=y+dy;
  if(xx<0||yy<0||xx>=(int)w->width||yy>=(int)w->height)continue;
  if(s->budget.collision_index->rows[yy]&(UINT64_C(1)<<xx))return true;
 }
 return false;
}
static bool heuristic_shoulder(struct lp_search *s,int x,int y) {
 /* The rear-axle inscribed disk is a lower-bound footprint. A center-only
  * road heuristic overlooks the return/excursion already forced by body width.
  * Cache this bounded classification in the temporary 2D nodes, not a new map. */
 int radius=(int)floorf((s->config->half_width+s->config->margin+s->config->tracking_guard)/s->world->resolution_m);
 if(radius>64)return true;
 for(int dy=-radius;dy<=radius;dy++) {
  s->metrics.road_checks++;
  int span=(int)floorf(sqrtf((float)(radius*radius-dy*dy))),row=y+dy;
  if(row<0||row>=(int)s->world->height||x-span<0||x+span>=(int)s->world->width)return true;
  uint64_t lo=~UINT64_C(0)<<(x-span),hi=x+span==63?~UINT64_C(0):(UINT64_C(1)<<(x+span+1))-1;
  if(s->budget.collision_index->shoulder[row]&lo&hi)return true;
 }
 return false;
}
static enum lp_result build_heuristic(struct lp_search *s) {
 const dw_world *w=s->world;unsigned n=w->width*w->height;
 dw_pose local=dw_compose(dw_inverse(w->origin),s->goal);
 if(!dw_pose_finite(local)||local.x<0||local.y<0||local.x>=w->width*w->resolution_m||local.y>=w->height*w->resolution_m)return LP_INPUT;
 int gx=(int)floorf(local.x/w->resolution_m),gy=(int)floorf(local.y/w->resolution_m);
 if(gx<0||gy<0||gx>=(int)w->width||gy>=(int)w->height)return LP_INPUT;
 s->building_heuristic=true;
 for(unsigned k=0;k<n;k++){
  if(!(k&63)){enum lp_result rc=guard(&s->budget,&s->metrics);if(rc!=LP_SEARCHING)return rc;}
  s->nodes[k].g=s->nodes[k].f=s->nodes[k].road_cost=FLT_MAX;
  s->nodes[k].heap=-1;s->nodes[k].closed=0;
  s->nodes[k].qx=(int16_t)(k%w->width);s->nodes[k].qy=(int16_t)(k/w->width);
  s->nodes[k].deviated=(w->required_features&DW_ROAD_PRIORITY)&&heuristic_shoulder(s,s->nodes[k].qx,s->nodes[k].qy);
 }
 int goal=gy*(int)w->width+gx;s->nodes[goal].g=s->nodes[goal].f=s->nodes[goal].road_cost=0;push(s,goal);
 while(s->open) {
  enum lp_result rc=guard(&s->budget,&s->metrics);if(rc!=LP_SEARCHING)return rc;
  int at=pop(s);s->nodes[at].closed=1;s->heuristic_expanded++;
  int x=at%(int)w->width,y=at/(int)w->width;
  for(int dy=-1;dy<=1;dy++)for(int dx=-1;dx<=1;dx++) {
   if((!dx&&!dy)||heuristic_blocked(s,x+dx,y+dy))continue;
   int id=(y+dy)*(int)w->width+x+dx;
   if(s->nodes[id].closed)continue;
   float step=w->resolution_m*(dx&&dy?1.41421356237f:1),next=s->nodes[at].g+step;
   float road=s->nodes[at].road_cost;
   if(w->required_features&DW_ROAD_PRIORITY)
    road+=step*.5f*(s->nodes[at].deviated+s->nodes[id].deviated);
   if(road>s->nodes[id].road_cost||(road==s->nodes[id].road_cost&&next>=s->nodes[id].g))continue;
   s->nodes[id].road_cost=road;
   s->nodes[id].g=s->nodes[id].f=next;
   if(s->nodes[id].heap<0)push(s,id);else up(s,(unsigned)s->nodes[id].heap);
  }
 }
 for(unsigned k=0;k<n;k++)s->heuristic[k]=(w->required_features&DW_ROAD_PRIORITY)?s->nodes[k].road_cost:s->nodes[k].g;
 s->building_heuristic=false;
 s->heuristic_open_high_water=s->metrics.open_high_water;s->metrics.open_high_water=0;
 memset(&s->nodes[0],0,sizeof(s->nodes[0]));return LP_SEARCHING;
}
static bool reached(const struct lp_search *s,const struct lp_node *n) {
 if(fabsf(dw_angle(n->pose.yaw-s->goal.yaw))>s->config->goal_heading)return false;
 if(s->signed_search)return n->gear==s->goal_gear&&distance(n->pose,s->goal)<=s->config->goal_distance&&
  fabsf(n->steer)<=s->config->max_steer/(LP_STEERS/2);
 if(n->parent<0&&distance(n->pose,s->goal)<=s->config->goal_distance)return true;
 /* Interior controls are spaced by two steering levels. Accept an actually
  * reached pose with the nearest road steering instead of appending a chord. */
 float steering_bin=s->config->max_steer/(LP_STEERS/2);
 if(distance(n->pose,s->goal)<=s->config->goal_distance&&fabsf(n->steer-s->goal_steer)<=steering_bin)return true;
 if(fabsf(s->goal_steer)>steering_bin)return false;
 if(fabsf(n->steer)>1e-5f)return false;
 dw_pose local=dw_compose(dw_inverse(n->pose),s->goal);
 /* A lateral bypass needs its settling segment. An ordinary small correction
  * near the reference does not need to drive away to manufacture that length.
  * Use the same resolution threshold as the path's maneuver classification. */
 float minimum=(s->preferred_direction||s->reference_obstructed)&&n->deviated?s->config->goal_straight_m:0;
 return local.x>=minimum&&
        local.x<=s->config->goal_straight_m+s->config->primitive_speed*s->config->primitive_duration&&
        fabsf(local.y)<=s->config->goal_distance;
}
/* Only blocked cells along the nominal road establish avoidance. Map edges and
 * road curvature are not obstacle evidence. All work uses the search budget. */
static enum lp_result obstruction(const dw_world *w,const struct tr_intent *task,dw_pose pose,
 struct lp_road_state goal,const struct lp_config *c,struct lp_budget budget,struct lp_metrics *metrics,bool *blocked_out){
 const dw_point *points;unsigned count;if(!reference(w,task,&points,&count))return LP_INPUT;
 *blocked_out=false;
 uint64_t any=0;for(unsigned y=0;y<w->height;y++)any|=budget.collision_index->rows[y];
 if(!any)return LP_PATH_FOUND;
 struct lp_road_state start;enum lp_result rc=lp_task_project(w,task,pose,&start);
 if(rc!=LP_PATH_FOUND)return rc;
 float arc=0,step=c->resolution*.5f;unsigned samples=0;
 for(unsigned k=1;k<count;k++) {
  dw_point a=points[k-1],b=points[k];
  float dx=b.x-a.x,dy=b.y-a.y,len=hypotf(dx,dy),end=fminf(len,goal.arc-arc);
  for(float at=fmaxf(0,start.arc-arc);at<=end;at+=step) {
   if(samples++>=c->collision_limit)return LP_WORK_BUDGET;
   dw_pose pose={a.x+dx*at/len,a.y+dy*at/len,atan2f(dy,dx)};
   rc=footprint(w,pose,c->tracking_guard+(task?task->vehicle_pad:0),c,metrics,&budget,true,NULL,NULL);
   if(rc==LP_COLLISION){*blocked_out=true;return LP_PATH_FOUND;}
   if(rc!=LP_PATH_FOUND)return rc;
  }
  arc+=len;if(arc>goal.arc)break;
 }
 return LP_PATH_FOUND;
}
enum lp_result lp_task_obstructed(const dw_world *w,const struct tr_intent *task,dw_pose pose,dw_pose goal,
 const struct lp_config *c,struct lp_budget budget,struct lp_metrics *metrics,bool *out){
 struct lp_road_state target;enum lp_result rc=lp_task_project(w,task,goal,&target);if(rc!=LP_PATH_FOUND)return rc;
 struct lp_collision_index index;
 if(!budget.collision_index){lp_index_build(&index,w,c);budget.collision_index=&index;}
 return obstruction(w,task,pose,target,c,budget,metrics,out);
}
static enum lp_result reference_obstruction(struct lp_search *s,struct lp_road_state goal){
 return obstruction(s->world,&s->task,s->start,goal,s->config,s->budget,&s->metrics,&s->reference_obstructed);
}

static enum lp_result local_terminal(struct lp_search *s) {
 const struct lp_config *c=s->config;
 float dt=c->primitive_duration/(LP_PRIMITIVE_SAMPLES-1);
 float radius=hypotf(fmaxf(c->front,c->rear)+c->margin,c->half_width+c->margin);
 float padding=.5f*(c->primitive_speed*dt+radius*fabsf(c->primitive_speed/c->wheelbase*tanf(c->max_steer)*dt));
 enum lp_result rc=footprint(s->world,s->goal,c->tracking_guard+padding,c,&s->metrics,&s->budget,false,NULL,&s->task);
 if(rc!=LP_COLLISION)return rc;
 float clearance=hypotf(c->front+c->margin+c->tracking_guard+.5f*c->primitive_speed*dt,
                       c->half_width+c->margin+c->tracking_guard+.5f*c->primitive_speed*dt);
 dw_pose previous=s->goal;
 /* A persistent merge milestone can be blocked in the new map. First consider
  * the full current local horizon, then retreat by the existing grid resolution.
  * This changes only the executable terminal, never the merge intent/proof. */
 for(float horizon=c->plan_horizon;horizon>c->goal_distance;horizon-=c->resolution) {
  rc=guard(&s->budget,&s->metrics);if(rc!=LP_SEARCHING)return rc;
  dw_pose candidate;rc=lp_task_goal(s->world,&s->task,s->start,horizon,clearance,&candidate);
  if(rc!=LP_PATH_FOUND)return rc;
  if(distance(previous,candidate)<1e-5f)continue;
  previous=candidate;
  if(distance(s->start,candidate)<=c->goal_distance)break;
  rc=footprint(s->world,candidate,c->tracking_guard+padding,c,&s->metrics,&s->budget,false,NULL,&s->task);
  if(rc==LP_PATH_FOUND){s->goal=candidate;return rc;}
  if(rc!=LP_COLLISION)return rc;
 }
 return LP_COLLISION;
}

static enum lp_result start(struct lp_search *s,const dw_world *w,dw_pose pose,float steer,dw_pose goal,
 const struct lp_config *c,const struct lp_assets *a,struct lp_budget budget,int preferred,bool local,
 const struct tr_intent *task,const struct tr_basis *basis,const struct lp_metrics *prefix,
 bool signed_mode,int initial_gear,int final_gear,unsigned max_cusps) {
 if(!s||!w||!c||!a||!dw_pose_finite(pose)||!dw_pose_finite(goal)||!isfinite(steer)||
    !c->node_limit||c->node_limit>LP_NODES||!c->expansion_limit||!c->collision_limit||c->steering_levels!=LP_STEERS||
    strcmp(c->id,w->configuration)||strcmp(a->sha256,w->primitive)||strcmp(c->id,a->id)||
    fabsf(c->resolution-w->resolution_m)>1e-6f||fabsf(c->resolution-a->resolution)>1e-6f||
    fabsf(c->wheelbase-a->wheelbase)>1e-6f||fabsf(c->max_steer-a->max_steer)>1e-6f||
    fabsf(c->steer_rate-a->steer_rate)>1e-6f||fabsf(c->primitive_duration-a->duration)>1e-6f||
    fabsf(c->primitive_speed-a->speed)>1e-6f||fabsf(steer)>c->max_steer)return LP_INPUT;
 memset(s,0,sizeof(*s));s->world=w;s->config=c;s->assets=a;s->budget=budget;s->start=pose;s->goal=goal;
 s->signed_search=signed_mode;s->initial_gear=initial_gear;s->goal_gear=final_gear;s->max_cusps=max_cusps;
 if(w->required_features&DW_LANE_MODEL){struct tr_view view;
  if(!task||!basis||!tr_open(w,task,&view)||!tr_basis_compatible(basis,w))return s->result=LP_INPUT;
  s->task=*task;s->basis=*basis;
 }
 if(prefix)s->metrics=*prefix;
 if(!budget.collision_index){lp_index_build(&s->collision_index,w,c);s->budget.collision_index=&s->collision_index;}
 s->initial_steer=steer;s->preferred_direction=preferred;s->result=LP_SEARCHING;
 for(unsigned k=0;k<LP_INDEX;k++)s->index[k]=-1;
 enum lp_result rc=footprint(w,pose,c->tracking_guard,c,&s->metrics,&s->budget,false,NULL,&s->task);
 if(rc!=LP_PATH_FOUND){s->result=rc;return rc;}
 if(local){rc=local_terminal(s);if(rc!=LP_PATH_FOUND)return s->result=rc;}
 if(!signed_mode){
  struct lp_road_state target;rc=lp_task_project(w,&s->task,s->goal,&target);
  if(rc!=LP_PATH_FOUND)return s->result=rc;
  s->goal_steer=atanf(c->wheelbase*target.curvature);
  rc=reference_obstruction(s,target);if(rc!=LP_PATH_FOUND)return s->result=rc;
 }
 rc=build_heuristic(s);if(rc!=LP_SEARCHING)return s->result=rc;
 struct lp_node *root=&s->nodes[0];root->pose=pose;root->steer=steer;root->parent=-1;root->heap=-1;
 root->gear=(int8_t)initial_gear;
 root->deviated=fabsf(reference_offset(w,&s->task,pose))>s->config->resolution;
 root->steering=(uint8_t)clamp(roundf(steer/c->max_steer*(LP_STEERS/2))+(LP_STEERS/2),0,LP_STEERS-1);root->f=heuristic(s,pose);
 quantize(s,root);bool exists;int slot=index_slot(s,root,&exists);
 if(slot<0)return LP_NODE_BUDGET;
 s->index[slot]=0;s->used=1;s->metrics.nodes_high_water=1;push(s,0);return LP_SEARCHING;
}
enum lp_result lp_start(struct lp_search *s,const dw_world *w,dw_pose pose,float steer,dw_pose goal,
 const struct lp_config *c,const struct lp_assets *a,struct lp_budget budget,int preferred) {
 return start(s,w,pose,steer,goal,c,a,budget,preferred,false,NULL,NULL,NULL,false,1,1,0);
}
enum lp_result lp_start_local(struct lp_search *s,const dw_world *w,dw_pose pose,float steer,dw_pose goal,
 const struct lp_config *c,const struct lp_assets *a,struct lp_budget budget,int preferred) {
 return start(s,w,pose,steer,goal,c,a,budget,preferred,true,NULL,NULL,NULL,false,1,1,0);
}
enum lp_result lp_start_task(struct lp_search *s,const dw_world *w,dw_pose pose,float steer,dw_pose goal,
 const struct lp_config *c,const struct lp_assets *a,struct lp_budget budget,const struct tr_intent *task,
 const struct tr_basis *basis,const struct lp_metrics *prefix){
 return start(s,w,pose,steer,goal,c,a,budget,0,true,task,basis,prefix,false,1,1,0);
}
enum lp_result lp_start_signed(struct lp_search *s,const dw_world *w,dw_pose pose,float steer,dw_pose goal,
 int initial,int final,unsigned cusps,const struct lp_config *c,const struct lp_assets *a,struct lp_budget budget){
 if((initial!=1&&initial!=-1)||(final!=1&&final!=-1)||cusps>2||!c||
    !isfinite(c->max_reverse_velocity)||c->max_reverse_velocity<=0)return LP_INPUT;
 return start(s,w,pose,steer,goal,c,a,budget,0,false,NULL,NULL,NULL,true,initial,final,cusps);
}
/* All ordinary edges use the generated control set. A measured root steering
 * between levels is connected by the same continuous ramp integration; it is
 * never snapped to a nominal initial curvature. */
static enum lp_result edge(struct lp_search *s,int parent,unsigned target,int gear,struct lp_node *next,
 xenova_drive_PathPoint *points,size_t *count) {
 const struct lp_node *old=&s->nodes[parent];const struct lp_config *c=s->config;
 const struct lp_primitive_point *samples=s->assets->motion[old->steering][target];
 float initial=c->max_steer*((int)old->steering-LP_STEERS/2)/(LP_STEERS/2);
 float final=c->max_steer*((int)target-LP_STEERS/2)/(LP_STEERS/2);
 float spacing=fminf(c->resolution*.25f,c->tracking_guard*.5f);
 if(!isfinite(spacing)||spacing<=0)return LP_INPUT;
 unsigned required=(unsigned)ceilf(c->primitive_speed*c->primitive_duration/spacing);
 unsigned steps=16*((required+15)/16);if(steps<16)steps=16;
 if(steps>64)return LP_WORK_BUDGET;
 float dt=c->primitive_duration/steps;
 if(fabsf(final-old->steer)/c->primitive_duration>c->steer_rate+1e-5f)return LP_COLLISION;
 bool custom=fabsf(old->steer-initial)>1e-5f||steps!=16;dw_pose pose=old->pose;
 float radius=hypotf(fmaxf(c->front,c->rear)+c->margin,c->half_width+c->margin);
 float yaw_step=c->primitive_speed/c->wheelbase*tanf(c->max_steer)*dt;
 float padding=.5f*(c->primitive_speed*dt+radius*fabsf(yaw_step));
 bool previous_outside=false;
 enum lp_result first=footprint(s->world,pose,padding+c->tracking_guard,c,&s->metrics,&s->budget,false,&previous_outside,&s->task);
 if(first!=LP_PATH_FOUND)return first;
 float road_cost=old->road_cost;
 for(unsigned k=1;k<=steps;k++) {
  enum lp_result rc=guard(&s->budget,&s->metrics);if(rc!=LP_SEARCHING)return rc;
  float steering=old->steer+(final-old->steer)*k/steps;
  if(custom)integrate(&pose,old->steer+(final-old->steer)*(k-.5f)/steps,gear*c->primitive_speed,dt,c->wheelbase);
  else pose=dw_compose(old->pose,(dw_pose){gear*samples[k].x,samples[k].y,gear*samples[k].yaw});
  /* Half the maximum translational+corner travel bounds the gap to an
   * adjacent sample. Both endpoint boxes cover that swept interval. */
  bool outside=false;
  rc=footprint(s->world,pose,padding+c->tracking_guard,c,&s->metrics,&s->budget,false,&outside,&s->task);
  if(rc!=LP_PATH_FOUND)return rc;
  road_cost+=c->primitive_speed*dt*.5f*(previous_outside+outside);previous_outside=outside;
  if(points&&k%(steps/16)==0){if(*count>=LP_PATH)return LP_PATH_LIMIT;
   float speed=gear>0?c->primitive_speed:fminf(c->primitive_speed,c->max_reverse_velocity);
   points[(*count)++]=(xenova_drive_PathPoint){.pose=pose,.steering_rad=steering,.velocity_mps=gear*speed};}
 }
 next->pose=pose;next->steer=final;next->steering=(uint8_t)target;next->road_cost=road_cost;
 next->gear=(int8_t)gear;next->cusps=(uint8_t)(old->cusps+(gear!=old->gear));return LP_PATH_FOUND;
}
static enum lp_result reconstruct_signed(struct lp_search *s,int last,struct lp_path *path){
 int chain[(LP_PATH-3)/16];unsigned count=0;int at=last;
 while(s->nodes[at].parent>=0){if(count>=sizeof(chain)/sizeof(*chain))return LP_PATH_LIMIT;chain[count++]=at;at=s->nodes[at].parent;}
 if(!count)return LP_STOP;
 memset(path,0,sizeof(*path));path->goal=path->merge_goal=s->goal;path->task=s->task;path->basis=s->basis;
 path->world_features=s->world->required_features;path->revision=path->maneuver_revision=1;path->count=1;
 path->points[0]=(xenova_drive_PathPoint){.pose=s->start,.steering_rad=s->initial_steer,.velocity_mps=0};
 path->execution.segments_count=1;
 path->execution.segments[0]=(xenova_drive_GearSegment){0,0,s->initial_gear};
 int gear=s->initial_gear;
 for(unsigned k=count;k>0;k--){
  int node=chain[k-1];const struct lp_node *next=&s->nodes[node];
  if(next->gear!=gear){
   unsigned previous=path->execution.segments_count-1;
   if(previous>=2||path->count<=path->execution.segments[previous].first_index+1||path->count>=LP_PATH)return LP_PATH_LIMIT;
   path->points[path->count-1].velocity_mps=0;
   path->execution.segments[previous].last_index=(uint32_t)path->count-1;
   path->points[path->count]=path->points[path->count-1];path->count++;
   path->execution.segments[path->execution.segments_count++]=(xenova_drive_GearSegment){(uint32_t)path->count-1,0,next->gear};
   gear=next->gear;
  }
  struct lp_node ignored={0};
  enum lp_result rc=edge(s,next->parent,next->edge,next->gear,&ignored,path->points,&path->count);
  if(rc!=LP_PATH_FOUND)return rc;
 }
 path->points[path->count-1].velocity_mps=0;
 path->execution.segments[path->execution.segments_count-1].last_index=(uint32_t)path->count-1;
 strcpy(path->frame,s->world->frame_epoch);strcpy(path->odom,s->world->odom_epoch);
 strcpy(path->configuration,s->world->configuration);strcpy(path->reference,s->world->reference_id);
 return lp_validate_path(path,s->world,s->config,&s->metrics,&s->budget);
}
static enum lp_result reconstruct(struct lp_search *s,int last,struct lp_path *path) {
 if(s->signed_search)return reconstruct_signed(s,last,path);
 /* A failed terminal connection must leave the open heap intact. At most 31
  * ordinary edges fit in the 512-point result, so this chain is 128 bytes. */
 int chain[LP_PATH/(LP_PRIMITIVE_SAMPLES-1)];
 unsigned n=0;int at=last;
 while(s->nodes[at].parent>=0){if(n>=sizeof(chain)/sizeof(*chain))return LP_PATH_LIMIT;chain[n++]=at;at=s->nodes[at].parent;}
 memset(path,0,sizeof(*path));path->count=1;path->goal=path->merge_goal=s->goal;
 path->task=s->task;path->basis=s->basis;
 path->world_features=s->world->required_features;
 path->points[0]=(xenova_drive_PathPoint){.pose=s->start,.steering_rad=s->initial_steer,.velocity_mps=s->config->primitive_speed};
 for(unsigned k=n;k>0;k--) {
  int node=chain[k-1];struct lp_node ignored={0};
  enum lp_result rc=edge(s,s->nodes[node].parent,s->nodes[node].edge,s->nodes[node].gear,&ignored,path->points,&path->count);
  if(rc!=LP_PATH_FOUND)return rc;
 }
 dw_pose end=s->nodes[last].pose,local=dw_compose(dw_inverse(end),s->goal);
 bool stationary=distance(end,s->goal)<=s->config->goal_distance;
 if(stationary&&!n)path->points[0].velocity_mps=0;
 float maximum_step=s->config->primitive_speed*s->config->primitive_duration/(LP_PRIMITIVE_SAMPLES-1);
 /* A moving primitive already inside the goal region is the real endpoint.
  * Only an already-stationary root needs the duplicate checked stop sample. */
 unsigned steps=stationary?(n?0:1):(unsigned)ceilf(local.x/maximum_step);
 if((!stationary&&!steps)||steps>LP_PATH-path->count)return LP_PATH_LIMIT;
 float ds=stationary?0:local.x/steps,padding=ds*.5f+s->config->tracking_guard;
 for(unsigned k=0;k<=steps;k++) {
  dw_pose pose={end.x+cosf(end.yaw)*ds*k,end.y+sinf(end.yaw)*ds*k,end.yaw};
  bool outside=false;
  enum lp_result rc=footprint(s->world,pose,padding,s->config,&s->metrics,&s->budget,false,&outside,&s->task);
  if(rc!=LP_PATH_FOUND)return rc;
  /* Decline a shoulder-cutting analytic tail; keep expanding real primitives.
   * This is a search decision, not a physical collision or a completed path. */
  if(steps&&outside)return LP_SEARCHING;
  if(k)path->points[path->count++]=(xenova_drive_PathPoint){.pose=pose,
   .steering_rad=stationary?s->nodes[last].steer:0,.velocity_mps=stationary?0:s->config->primitive_speed};
 }
 strcpy(path->frame,s->world->frame_epoch);strcpy(path->odom,s->world->odom_epoch);
 strcpy(path->configuration,s->world->configuration);strcpy(path->reference,s->world->reference_id);
 float furthest=0;
 for(size_t k=0;k<path->count;k++){float offset=reference_offset(s->world,&s->task,path->points[k].pose);if(fabsf(offset)>fabsf(furthest))furthest=offset;}
 path->direction=s->reference_obstructed&&fabsf(furthest)>s->config->resolution?(furthest>0?1:-1):0;
 /* A return correction without a newly blocked nominal corridor continues the
  * accepted maneuver; its steering sign is not a new avoidance decision. */
 if(s->preferred_direction&&!s->reference_obstructed)path->direction=s->preferred_direction;
 if(s->task.active&&s->task.changing){
  float offset=reference_offset(s->world,&s->task,s->start);
  path->direction=offset<0?1:-1;
 }
 path->phase=path->direction?1:0;path->revision=path->maneuver_revision=1;
 return LP_PATH_FOUND;
}
static enum lp_result expand_edge(struct lp_search *s,int current,unsigned target,int gear){
 struct lp_node *node=&s->nodes[current];
 if(gear!=node->gear&&(node->parent<0||node->cusps>=s->max_cusps))return LP_SEARCHING;
 struct lp_node next={.parent=current,.heap=-1,.edge=(uint8_t)target};s->metrics.primitives++;
 enum lp_result rc=edge(s,current,target,gear,&next,NULL,NULL);
 if(rc==LP_COLLISION)return LP_SEARCHING;
 if(rc!=LP_PATH_FOUND)return rc;
 quantize(s,&next);float offset=reference_offset(s->world,&s->task,next.pose);
 next.deviated=node->deviated||fabsf(offset)>s->config->resolution;
 float change=s->preferred_direction&&offset*s->preferred_direction< -s->config->resolution?s->config->switch_cost:0;
 if(gear!=node->gear)change+=s->config->switch_cost;
 next.g=node->g+s->config->primitive_speed*s->config->primitive_duration+
        s->config->deviation_cost*offset*offset+s->config->curvature_cost*fabsf(next.steer)+change;
 next.f=next.g+heuristic(s,next.pose);
 if(!isfinite(next.f)||!isfinite(next.road_cost))return LP_NUMERIC;
 bool exists;int slot=index_slot(s,&next,&exists);
 if(slot<0)return LP_NODE_BUDGET;
 if(exists){
  int id=s->index[slot];struct lp_node *prior=&s->nodes[id];
  if(prior->road_cost<next.road_cost-1e-6f||
     (fabsf(prior->road_cost-next.road_cost)<=1e-6f&&prior->g<=next.g+1e-6f))return LP_SEARCHING;
  next.heap=prior->heap;*prior=next;
  if(prior->heap<0)push(s,id);else up(s,(unsigned)prior->heap);
 }else{
  if(s->used>=s->config->node_limit)return LP_NODE_BUDGET;
  int id=(int)s->used++;s->nodes[id]=next;s->index[slot]=id;push(s,id);
  if(s->used>s->metrics.nodes_high_water)s->metrics.nodes_high_water=s->used;
 }
 return LP_SEARCHING;
}
enum lp_result lp_advance(struct lp_search *s,unsigned expansions,struct lp_path *path){
 if(s->result!=LP_SEARCHING)return s->result;
 if(!expansions||expansions>32)return LP_INPUT;
 for(unsigned step=0;step<expansions;step++){
  enum lp_result rc=guard(&s->budget,&s->metrics);if(rc!=LP_SEARCHING)return s->result=rc;
  if(!s->open)return s->result=LP_NOT_FOUND;
  if(s->metrics.expanded>=s->config->expansion_limit)return s->result=LP_WORK_BUDGET;
  int current=pop(s);struct lp_node *node=&s->nodes[current];node->closed=1;s->metrics.expanded++;
  if(reached(s,node)){
   rc=reconstruct(s,current,path);
   if(rc!=LP_COLLISION&&rc!=LP_SEARCHING)return s->result=rc;
   path->count=0;
  }
  int original_gear=node->gear;
  for(unsigned target=0;target<LP_STEERS;target++){
   if(current!=0&&(target&1))continue;
   rc=expand_edge(s,current,target,original_gear);if(rc!=LP_SEARCHING)return s->result=rc;
   if(s->signed_search){rc=expand_edge(s,current,target,-original_gear);if(rc!=LP_SEARCHING)return s->result=rc;}
  }
 }
 return LP_SEARCHING;
}
static unsigned path_segments(const struct lp_path *path){return path->execution.segments_count?path->execution.segments_count:1;}
static xenova_drive_GearSegment path_segment(const struct lp_path *path,unsigned index){
 if(path->execution.segments_count)return path->execution.segments[index];
 return (xenova_drive_GearSegment){0,(uint32_t)path->count-1,1};
}
static bool same_pose(dw_pose a,dw_pose b){return a.x==b.x&&a.y==b.y&&a.yaw==b.yaw;}
static bool path_shape(const struct lp_path *path){
 if(!path||path->count<2||path->count>LP_PATH||path->execution.segments_count>3)return false;
 unsigned count=path_segments(path);
 for(unsigned part=0;part<count;part++){
  xenova_drive_GearSegment segment=path_segment(path,part);
  if((segment.signed_direction!=1&&segment.signed_direction!=-1)||segment.last_index>=path->count||
     segment.last_index<=segment.first_index||segment.first_index!=(part?path_segment(path,part-1).last_index+1:0))return false;
  if(part){xenova_drive_GearSegment previous=path_segment(path,part-1);
   if(previous.signed_direction==segment.signed_direction||
      !same_pose(path->points[previous.last_index].pose,path->points[segment.first_index].pose)||
      path->points[previous.last_index].velocity_mps||path->points[segment.first_index].velocity_mps)return false;
  }
  float length=0;
  for(size_t i=segment.first_index;i<=segment.last_index;i++){
   if(path->points[i].velocity_mps*segment.signed_direction<0)return false;
   if(i>segment.first_index)length+=distance(path->points[i-1].pose,path->points[i].pose);
  }
  if(!isfinite(length)||length<=0)return false;
 }
 if(path_segment(path,count-1).last_index!=path->count-1)return false;
 for(size_t i=0;i<path->count;i++)for(size_t j=0;j<i;j++)if(same_pose(path->points[i].pose,path->points[j].pose)){
  bool cusp=false;for(unsigned part=1;part<count;part++)if(i==path_segment(path,part).first_index&&j+1==i)cusp=true;
  if(!cusp)return false;
 }
 return true;
}
enum lp_result lp_validate_path(const struct lp_path *p,const dw_world *w,const struct lp_config *c,
 struct lp_metrics *m,const struct lp_budget *b){
 if(!path_shape(p)||!c||!w)return LP_INPUT;
 if((w->required_features&DW_LANE_MODEL)&&(!p->task.active||!tr_basis_compatible(&p->basis,w)))return LP_INPUT;
 if(p->world_features!=w->required_features||strcmp(p->frame,w->frame_epoch)||strcmp(p->odom,w->odom_epoch)||
    strcmp(p->configuration,w->configuration)||strcmp(p->reference,w->reference_id))return LP_INPUT;
 float radius=hypotf(fmaxf(c->front,c->rear),c->half_width);
 for(unsigned part=0;part<path_segments(p);part++){
  xenova_drive_GearSegment segment=path_segment(p,part);float vmax=segment.signed_direction>0?c->max_velocity:c->max_reverse_velocity;
  if(!isfinite(vmax)||vmax<=0)return LP_INPUT;
  for(size_t k=segment.first_index;k<=segment.last_index;k++){
   const xenova_drive_PathPoint *point=&p->points[k];
   if(!dw_pose_finite(point->pose)||!isfinite(point->steering_rad)||fabsf(point->steering_rad)>c->max_steer+1e-5f||
      !isfinite(point->velocity_mps)||fabsf(point->velocity_mps)>vmax+1e-5f)return LP_INPUT;
   size_t previous=k==segment.first_index?k:k-1;
   float ds=distance(point->pose,p->points[previous].pose),dy=fabsf(dw_angle(point->pose.yaw-p->points[previous].pose.yaw));
   float pad=.5f*(ds+radius*dy);
   if(k==segment.first_index){
    float first_ds=distance(p->points[k].pose,p->points[k+1].pose);
    float first_yaw=fabsf(dw_angle(p->points[k+1].pose.yaw-p->points[k].pose.yaw));pad=.5f*(first_ds+radius*first_yaw);
   }else{
    if(ds<=0||ds>c->primitive_speed*c->primitive_duration/16*1.05f||
       dy>ds*tanf(c->max_steer)/c->wheelbase*1.1f+.0001f)return LP_INPUT;
    float interval=ds/c->primitive_speed;
    if(fabsf(point->steering_rad-p->points[previous].steering_rad)>c->steer_rate*interval+.0001f)return LP_INPUT;
    dw_pose predicted=p->points[previous].pose;
    integrate(&predicted,.5f*(point->steering_rad+p->points[previous].steering_rad),segment.signed_direction*c->primitive_speed,interval,c->wheelbase);
    if(distance(predicted,point->pose)>.001f||fabsf(dw_angle(predicted.yaw-point->pose.yaw))>.001f)return LP_INPUT;
   }
   enum lp_result rc=footprint(w,point->pose,pad+c->tracking_guard,c,m,b,false,NULL,&p->task);if(rc!=LP_PATH_FOUND)return rc;
  }
 }
 return LP_PATH_FOUND;
}
/* Signed current-state reaction/braking rollout. known is travelled length,
 * never signed displacement; the final clamped command gets a fresh rollout. */
static enum lp_result project(const dw_world *w,dw_pose pose,float current_velocity,float current_steer,
 float velocity,float steer,float uncertainty,float steering_error,const struct lp_config *c,struct lp_metrics *m,
 const struct lp_budget *b,float *known,const struct tr_intent *task,dw_pose *stopped,float *stop_pad){
 if(current_velocity*velocity<0)return LP_INPUT;
 float maximum=fmaxf(c->max_velocity,c->max_reverse_velocity);
 float interval=fminf(.025f,c->resolution/(4*fmaxf(maximum,.01f)));
 float time=0,v=current_velocity,delta=current_steer;*known=0;
 float horizon=c->reaction_s+fmaxf(fabsf(v),fabsf(velocity))/c->max_brake+.1f;
 float yaw_error=maximum/c->wheelbase*horizon*
  fmaxf(tanf(fabsf(current_steer)+steering_error)-tanf(fabsf(current_steer)),
        tanf(fabsf(steer)+steering_error)-tanf(fabsf(steer)));
 unsigned steps=(unsigned)ceilf(horizon/interval);if(steps>512)return LP_WORK_BUDGET;
 float radius=hypotf(fmaxf(c->front,c->rear),c->half_width),last_pad=0;
 for(unsigned k=0;k<=steps;k++){
  float extra=.5f*(fmaxf(fabsf(v),fabsf(velocity))*interval+radius*maximum/c->wheelbase*fabsf(tanf(c->max_steer))*interval);
  float envelope=(maximum*time+radius)*yaw_error;last_pad=extra+uncertainty+envelope;
  enum lp_result rc=footprint(w,pose,last_pad,c,m,b,false,NULL,task);if(rc!=LP_PATH_FOUND)return rc;
  delta+=clamp(steer-delta,-c->steer_rate*interval,c->steer_rate*interval);
  int gear=v<0?-1:v>0?1:velocity<0?-1:1;
  float magnitude=fabsf(v),target=fabsf(velocity);
  if(time<c->reaction_s)magnitude+=clamp(target-magnitude,-c->max_brake*interval,c->max_accel*interval);
  else magnitude=fmaxf(0,magnitude-c->max_brake*interval);
  v=gear*magnitude;integrate(&pose,delta,v,interval,c->wheelbase);*known+=fabsf(v)*interval;time+=interval;
 }
 if(stopped)*stopped=pose;
 if(stop_pad)*stop_pad=last_pad;
 return LP_PATH_FOUND;
}
float lp_path_remaining(const struct lp_path *path,dw_pose pose,size_t *nearest){
 if(!path||path->count<2||path->count>LP_PATH||!dw_pose_finite(pose)||path_segments(path)>1)return NAN;
 float best=FLT_MAX;size_t index=0;
 for(size_t k=0;k<path->count;k++){float d=distance(pose,path->points[k].pose);if(d<best){best=d;index=k;}}
 float remaining=best;for(size_t k=index+1;k<path->count;k++)remaining+=distance(path->points[k-1].pose,path->points[k].pose);
 if(nearest)*nearest=index;
 return remaining;
}
void lp_tracker_begin(struct lp_tracker *tracker,uint64_t token){
 if(tracker->path_token==token)return;
 memset(tracker,0,sizeof(*tracker));tracker->path_token=token;
}
static float arc_to(const struct lp_path *path,xenova_drive_GearSegment segment,size_t end){
 float arc=0;for(size_t i=segment.first_index+1;i<=end;i++)arc+=distance(path->points[i-1].pose,path->points[i].pose);return arc;
}
bool lp_tracker_next_segment(struct lp_tracker *tracker,const struct lp_path *path,uint64_t now,uint64_t original_stop,
 const struct lp_direction_fact *fact){
 if(!tracker||!path||!fact||!tracker->initialized||tracker->segment+1>=path_segments(path)||!original_stop||
    !fact->current||!fact->stationary||!fact->steering_settled||fact->expires_pi_us<=now||
    fact->path_revision!=path->revision||fact->maneuver_revision!=path->maneuver_revision||
    fact->calculation_sequence!=original_stop||!fact->serial_stop_sequence||!fact->gate_revision||fact->gate_revision<=tracker->gate_revision||
    fact->next_segment!=tracker->segment+1||fact->direction!=path_segment(path,fact->next_segment).signed_direction)return false;
 xenova_drive_GearSegment old=path_segment(path,tracker->segment);
 if(tracker->progress+1e-4f<arc_to(path,old,old.last_index))return false;
 tracker->segment++;tracker->index=path_segment(path,tracker->segment).first_index;tracker->progress=0;
 tracker->gate_revision=fact->gate_revision;tracker->last_pi_us=0;tracker->last_velocity=0;return true;
}
static bool progress_project(struct lp_tracker *tracker,const struct lp_path *path,dw_pose pose,const dw_vehicle *vehicle,
 const struct lp_config *c,float dt,float *remaining,bool *terminal_in_window){
 if(!tracker->initialized){
  tracker->segment=path->execution.present?path->execution.mission.active_segment:0;
  if(tracker->segment>=path_segments(path))return false;
  xenova_drive_GearSegment segment=path_segment(path,tracker->segment);
  tracker->index=path->initial_tracker_index;
  if(tracker->index<segment.first_index||tracker->index>segment.last_index)return false;
  tracker->progress=arc_to(path,segment,tracker->index);tracker->last_pi_us=path->initial_pi_us;
 }
 xenova_drive_GearSegment segment=path_segment(path,tracker->segment);float total=arc_to(path,segment,segment.last_index);
 float elapsed=dt;
 if(vehicle->pi_sample_us&&tracker->last_pi_us){
  if(vehicle->pi_sample_us<tracker->last_pi_us)return false;
  elapsed=(float)((double)(vehicle->pi_sample_us-tracker->last_pi_us)/1000000.);
  if(elapsed>c->pose_age_us/1000000.f)return false;
 }
 float radius=hypotf(fmaxf(c->front,c->rear)+c->margin,c->half_width+c->margin);
 float error=c->max_position_error+vehicle->position_error_m+radius*(c->max_heading_error+vehicle->heading_error_rad)+
  c->primitive_speed*c->primitive_duration/16;
 float low=fmaxf(0,tracker->progress-error),high=fminf(total,tracker->progress+fmaxf(c->max_velocity,c->max_reverse_velocity)*elapsed+.5f*c->max_accel*elapsed*elapsed+error);
 if(!tracker->initialized&&!path->initial_pi_us){low=0;high=total;} /* explicit software/new-plan first correspondence */
 float best=FLT_MAX,best_s=0,arc=0;size_t best_index=0;bool found=false,ambiguous=false;
 for(size_t i=segment.first_index;i<segment.last_index;i++){
  dw_pose a=path->points[i].pose,z=path->points[i+1].pose;float dx=z.x-a.x,dy=z.y-a.y,length=hypotf(dx,dy);
  if(length<=0||!isfinite(length))return false;
  float begin=fmaxf(low,arc),end=fminf(high,arc+length);
  if(begin<=end){
   float u=clamp(((pose.x-a.x)*dx+(pose.y-a.y)*dy)/(length*length),(begin-arc)/length,(end-arc)/length);
   float heading=dw_angle(a.yaw+u*dw_angle(z.yaw-a.yaw));
   if(fabsf(dw_angle(pose.yaw-heading))+vehicle->heading_error_rad<=c->goal_heading+c->max_heading_error){
    float px=a.x+u*dx-pose.x,py=a.y+u*dy-pose.y,d2=px*px+py*py,station=arc+u*length;
    if(found&&fabsf(d2-best)<=vehicle->position_error_m*vehicle->position_error_m&&fabsf(station-best_s)>2*error)ambiguous=true;
    if(d2<best){best=d2;best_s=station;best_index=i;found=true;}
   }
  }
  arc+=length;
 }
 if(!found||ambiguous||sqrtf(best)>c->tracking_error)return false;
 tracker->progress=fmaxf(tracker->progress,best_s);tracker->index=best_index;
 tracker->initialized=true;if(vehicle->pi_sample_us)tracker->last_pi_us=vehicle->pi_sample_us;
 *remaining=fmaxf(0,total-tracker->progress);*terminal_in_window=high+1e-4f>=total;return true;
}
static dw_pose carrot(const struct lp_path *path,xenova_drive_GearSegment segment,float station){
 float arc=0;
 for(size_t i=segment.first_index;i<segment.last_index;i++){
  dw_pose a=path->points[i].pose,z=path->points[i+1].pose;float length=distance(a,z);
  if(station<=arc+length){float u=clamp((station-arc)/length,0,1);
   return (dw_pose){a.x+u*(z.x-a.x),a.y+u*(z.y-a.y),dw_angle(a.yaw+u*dw_angle(z.yaw-a.yaw))};}
  arc+=length;
 }
 return path->points[segment.last_index].pose;
}
enum lp_result lp_track(struct lp_tracker *tracker,const struct lp_path *path,const dw_world *w,dw_pose pose,
 const dw_vehicle *vehicle,const struct lp_config *c,float dt,struct lp_command *out,struct lp_metrics *m,const struct lp_budget *b){
 memset(out,0,sizeof(*out));out->result=LP_INPUT;
 if(!path_shape(path)||!dw_pose_finite(pose)||!isfinite(dt)||dt<=0||dt>.1f||
    !isfinite(vehicle->velocity_mps)||vehicle->velocity_mps< -c->max_reverse_velocity-.01f||vehicle->velocity_mps>c->max_velocity+.01f||
    !isfinite(vehicle->position_error_m)||vehicle->position_error_m<0||vehicle->position_error_m>c->max_position_error||
    !isfinite(vehicle->heading_error_rad)||vehicle->heading_error_rad<0||vehicle->heading_error_rad>c->max_heading_error||
    !vehicle->has_steering_error_rad||!isfinite(vehicle->steering_error_rad)||vehicle->steering_error_rad<0||
    vehicle->steering_error_rad>c->max_steering_error||vehicle->quality!=3||path->world_features!=w->required_features||
    strcmp(path->frame,w->frame_epoch)||strcmp(path->odom,w->odom_epoch)||strcmp(path->configuration,w->configuration)||strcmp(path->reference,w->reference_id))return LP_INPUT;
 if((w->required_features&DW_LANE_MODEL)&&(!path->task.active||!tr_basis_compatible(&path->basis,w)))return LP_INPUT;
 float steering=vehicle->has_measured_steering_rad?vehicle->measured_steering_rad:vehicle->has_estimated_steering_rad?vehicle->estimated_steering_rad:NAN;
 if(!isfinite(steering)||fabsf(steering)+vehicle->steering_error_rad>c->max_steer+.0001f)return LP_INPUT;
 float radius=hypotf(fmaxf(c->front,c->rear)+c->margin,c->half_width+c->margin);
 float uncertainty=vehicle->position_error_m+radius*vehicle->heading_error_rad,remaining;bool terminal_in_window;
 if(!progress_project(tracker,path,pose,vehicle,c,dt,&remaining,&terminal_in_window))return LP_INPUT;
 xenova_drive_GearSegment segment=path_segment(path,tracker->segment);int gear=segment.signed_direction;
 if(vehicle->velocity_mps*gear< -.01f)return LP_INPUT;
 out->segment_index=tracker->segment;out->cusp_index=tracker->segment;out->requested_direction=gear;
 struct tr_intent live_task=path->task;live_task.vehicle_pad=0;
 dw_pose goal=path->points[segment.last_index].pose;
 if(tracker->segment+1==path_segments(path))goal=path->goal;
 if(path->direction){
  if(path->task.active)out->maneuver_phase=tr_unique_lane(w,pose,uncertainty+c->tracking_guard,c)==path->task.selected_lane_id?2:1;
  else out->maneuver_phase=path->phase;
 }
 float known=0;dw_pose stopped;float final_pad=0;
 enum lp_result stopped_result=project(w,pose,vehicle->velocity_mps,steering,0,steering,uncertainty,vehicle->steering_error_rad,c,m,b,&known,&live_task,&stopped,&final_pad);
 /* 종점에 가까운 입력도 같은 원 progress window를 통과해야 한다. 현재 pose만으로
  * 아직 도달할 수 없는 남은 arc를 지워 segment/cusp 완료를 만들지 않는다. */
 bool captured=terminal_in_window&&stopped_result==LP_PATH_FOUND&&distance(pose,goal)+vehicle->position_error_m<=c->goal_distance&&
  distance(stopped,goal)+final_pad<=c->goal_distance&&fabsf(dw_angle(pose.yaw-goal.yaw))+vehicle->heading_error_rad<=c->goal_heading&&
  fabsf(dw_angle(stopped.yaw-goal.yaw))+vehicle->heading_error_rad<=c->goal_heading;
 if(captured){
  tracker->progress=arc_to(path,segment,segment.last_index);tracker->index=segment.last_index;
  out->steer=steering;out->emergency=true;out->known_distance=known;out->result=LP_STOP;
  if(tracker->segment+1<path_segments(path)){out->gate_required=true;out->requested_direction=path_segment(path,tracker->segment+1).signed_direction;}
  tracker->completed++;return LP_STOP;
 }
 if(stopped_result!=LP_PATH_FOUND)return out->result=stopped_result;
 float lookahead=clamp(fabsf(vehicle->velocity_mps)*c->lookahead_time,c->lookahead_min,c->lookahead_max);
 dw_pose target=carrot(path,segment,tracker->progress+lookahead),local=dw_compose(dw_inverse(pose),target);
 float chord2=local.x*local.x+local.y*local.y;
 if(chord2<1e-8f||gear*local.x<=0)return LP_INPUT;
 out->target_x=local.x;out->target_y=local.y;out->geometric_distance=sqrtf(chord2);out->curvature=2*local.y/chord2;
 float allowed=c->max_steer-vehicle->steering_error_rad,delta=clamp(atanf(c->wheelbase*out->curvature),-allowed,allowed);
 delta=steering+clamp(delta-steering,-c->steer_rate*dt,c->steer_rate*dt);
 float speed=gear>0?c->max_velocity:c->max_reverse_velocity;
 if(fabsf(out->curvature)>1e-6f)speed=fminf(speed,sqrtf(c->max_lateral_accel/fabsf(out->curvature)));
 for(size_t i=segment.first_index+1;i<=segment.last_index;i++){
  float ds=distance(path->points[i-1].pose,path->points[i].pose),slope=fabsf(path->points[i].steering_rad-path->points[i-1].steering_rad)/ds;
  if(slope>1e-6f)speed=fminf(speed,c->steer_rate/slope);
 }
 float reaction=fabsf(vehicle->velocity_mps)*c->reaction_s+.5f*c->max_accel*c->reaction_s*c->reaction_s;
 speed=fminf(speed,sqrtf(2*c->max_brake*fmaxf(0,remaining-reaction-vehicle->position_error_m)));
 enum lp_result rc=project(w,pose,vehicle->velocity_mps,steering,gear*speed,delta,uncertainty,vehicle->steering_error_rad,c,m,b,&known,&live_task,NULL,NULL);
 if(rc!=LP_PATH_FOUND&&rc!=LP_COLLISION)return out->result=rc;
 out->known_distance=known;
 if(rc==LP_COLLISION){
  float safe=fmaxf(0,known-c->margin-vehicle->position_error_m);
  float limit=sqrtf(c->max_brake*c->max_brake*c->reaction_s*c->reaction_s+2*c->max_brake*safe)-c->max_brake*c->reaction_s;
  speed=fminf(speed,limit);
 }
 float actual=fabsf(vehicle->velocity_mps),limited=actual+clamp(speed-actual,-c->max_brake*dt,c->max_accel*dt);
 if(speed<actual-c->max_brake*dt){limited=0;out->emergency=true;}
 rc=project(w,pose,vehicle->velocity_mps,steering,gear*limited,delta,uncertainty,vehicle->steering_error_rad,c,m,b,&known,&live_task,NULL,NULL);
 if(rc!=LP_PATH_FOUND){out->steer=steering;out->emergency=true;return out->result=rc;}
 out->velocity=gear*limited;out->steer=delta;out->result=LP_PATH_FOUND;
 tracker->last_velocity=out->velocity;tracker->last_steer=delta;tracker->completed++;return LP_PATH_FOUND;
}
const char *lp_reason(enum lp_result result) {
 static const char *const names[]={"SEARCHING","PATH_FOUND","NORMAL_STOP","SEARCH_NOT_FOUND","NODE_BUDGET",
  "WORK_BUDGET","DEADLINE","CANCELLED","INPUT_INVALID","NUMERIC_ERROR","COLLISION","PATH_LIMIT","CLOCK_ERROR"};
 return (unsigned)result<sizeof(names)/sizeof(*names)?names[result]:"INTERNAL_ERROR";
}
