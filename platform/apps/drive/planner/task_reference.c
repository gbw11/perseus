/* SPDX-License-Identifier: Apache-2.0 */
#include "task_reference.h"
#include "lattice.h"
#include <math.h>
#include <string.h>

static bool corridor_simple(const struct tr_view *);

static const xenova_drive_LaneGeometry *lane(const dw_world *w,uint32_t id){
 const xenova_drive_LaneGeometry *found=NULL;
 for(unsigned i=0;i<w->lane_model.lanes_count;i++)if(w->lane_model.lanes[i].lane_id==id){
  if(found)return NULL;
  found=&w->lane_model.lanes[i];
 }
 return found;
}
bool tr_open(const dw_world *w,const struct tr_intent *intent,struct tr_view *out){
 if(!w||!intent||!out||!intent->active||!(w->required_features&DW_LANE_MODEL)||!w->has_lane_model||
    w->lane_model.lanes_count!=2||w->lane_model.divider_count<2||w->lane_model.divider_count>32||
    !isfinite(intent->vehicle_pad)||intent->vehicle_pad<0)return false;
 const xenova_drive_LaneGeometry *to=lane(w,intent->selected_lane_id),*from=lane(w,intent->from_lane_id);
 unsigned n=w->lane_model.divider_count;
 if(!to||!from||!lane(w,intent->home_lane_id)||to->centerline_count!=n||from->centerline_count!=n||
    to->travel_direction!=1||from->travel_direction!=1||to->point_state.size!=n||from->point_state.size!=n||
    w->lane_model.crossing.size!=n-1||w->lane_model.divider_state.size!=n||
    (intent->changing?(to==from||to->adjacent_lane_id!=from->lane_id||from->adjacent_lane_id!=to->lane_id):to!=from))return false;
 /* 이 첫 실행 profile은 관측한 공동 station만 사용한다. 추정 끝을 경계 승인으로 쓰지 않는다. */
 for(unsigned i=0;i<n;i++)if(to->point_state.bytes[i]||from->point_state.bytes[i]||w->lane_model.divider_state.bytes[i])return false;
 struct tr_view result={w,to,from,to->centerline,n};
 if(to!=from&&!corridor_simple(&result))return false;
 *out=result;return true;
}
static dw_point vertex(const struct tr_view *v,unsigned i){
 unsigned n=v->count;const xenova_drive_LaneModel *m=&v->world->lane_model;
 if(i<n){dw_point c=v->selected->centerline[i],d=m->divider[i];return (dw_point){2*c.x-d.x,2*c.y-d.y};}
 unsigned j=2*n-1-i;dw_point d=m->divider[j];
 if(v->selected==v->from)return d;
 dw_point c=v->from->centerline[j];return (dw_point){2*c.x-d.x,2*c.y-d.y};
}
static bool inside(const struct tr_view *v,dw_point p){
 bool hit=false;unsigned n=2*v->count;
 for(unsigned i=0,j=n-1;i<n;j=i++){
  dw_point a=vertex(v,i),b=vertex(v,j);
  if(((a.y>p.y)!=(b.y>p.y))&&p.x<(b.x-a.x)*(p.y-a.y)/(b.y-a.y)+a.x)hit=!hit;
 }
 return hit;
}
static bool cuts(dw_point a,dw_point b,dw_pose center,float half,float width){
 float cs=cosf(center.yaw),sn=sinf(center.yaw);
 float ax=(a.x-center.x)*cs+(a.y-center.y)*sn,ay=-(a.x-center.x)*sn+(a.y-center.y)*cs;
 float bx=(b.x-center.x)*cs+(b.y-center.y)*sn,by=-(b.x-center.x)*sn+(b.y-center.y)*cs;
 float lo=0,hi=1,first[2]={ax,ay},delta[2]={bx-ax,by-ay},bounds[2]={half,width};
 for(unsigned k=0;k<2;k++){
  if(fabsf(delta[k])<1e-9f){if(fabsf(first[k])>=bounds[k])return false;}
  else{float l=(-bounds[k]-first[k])/delta[k],h=(bounds[k]-first[k])/delta[k];
   if(l>h){float t=l;l=h;h=t;}lo=fmaxf(lo,l);hi=fminf(hi,h);if(lo>hi)return false;}
 }
 return lo<=hi;
}
bool tr_contains(const struct tr_view *v,dw_pose pose,float extra,const struct lp_config *c){
 if(!v||!c||!dw_pose_finite(pose)||!isfinite(extra)||extra<0)return false;
 const xenova_drive_LaneModel *m=&v->world->lane_model;
 float position=fmaxf(v->selected->position_bound_m,v->from->position_bound_m);
 float heading=fmaxf(v->selected->heading_bound_rad,v->from->heading_bound_rad);
 float radius=hypotf(fmaxf(c->front,c->rear)+c->margin,c->half_width+c->margin);
 /* outer=2*center-divider이므로 원 위치 오차도 같은 식으로 보존한다. */
 float error=2*position+m->divider_position_bound_m+radius*(2*heading+m->divider_heading_bound_rad);
 if(!isfinite(error)||error<0)return false;
 float half=(c->front+c->rear)*.5f+c->margin+extra+error,width=c->half_width+c->margin+extra+error;
 dw_pose center=pose;float shift=(c->front-c->rear)*.5f;
 center.x+=shift*cosf(pose.yaw);center.y+=shift*sinf(pose.yaw);
 for(unsigned i=0;i<4;i++){
  float u=i&1?half:-half,z=i&2?width:-width;
  if(!inside(v,(dw_point){center.x+cosf(pose.yaw)*u-sinf(pose.yaw)*z,
                         center.y+sinf(pose.yaw)*u+cosf(pose.yaw)*z}))return false;
 }
 for(unsigned i=0;i<2*v->count;i++)if(cuts(vertex(v,i),vertex(v,(i+1)%(2*v->count)),center,half,width))return false;
 for(unsigned i=0;i+1<v->count;i++){
  if(cuts(m->divider[i],m->divider[i+1],center,half,width)&&
     (v->selected==v->from||m->crossing.bytes[i]!=1||m->divider_state.bytes[i]||m->divider_state.bytes[i+1]))return false;
 }
 return true;
}
uint32_t tr_unique_lane(const dw_world *w,dw_pose pose,float extra,const struct lp_config *c){
 if(!w||!w->has_lane_model||w->lane_model.lanes_count!=2)return 0;
 uint32_t selected=0;
 for(unsigned i=0;i<2;i++){
  uint32_t id=w->lane_model.lanes[i].lane_id;struct tr_intent intent={true,false,id,id,id,0};struct tr_view view;
  if(tr_open(w,&intent,&view)&&tr_contains(&view,pose,extra,c)){if(selected)return 0;selected=id;}
 }
 return selected;
}
bool tr_same_intent(const struct tr_intent *a,const struct tr_intent *b){
 return a->active==b->active&&a->changing==b->changing&&a->selected_lane_id==b->selected_lane_id&&
        a->from_lane_id==b->from_lane_id&&a->home_lane_id==b->home_lane_id;
}
static float orient(dw_point a,dw_point b,dw_point c){return (b.x-a.x)*(c.y-a.y)-(b.y-a.y)*(c.x-a.x);}
static bool overlap(dw_point a,dw_point b,dw_point c,dw_point d){
 return fmaxf(fminf(a.x,b.x),fminf(c.x,d.x))<=fminf(fmaxf(a.x,b.x),fmaxf(c.x,d.x))&&
        fmaxf(fminf(a.y,b.y),fminf(c.y,d.y))<=fminf(fmaxf(a.y,b.y),fmaxf(c.y,d.y))&&
        orient(a,b,c)*orient(a,b,d)<=0&&orient(c,d,a)*orient(c,d,b)<=0;
}
static bool corridor_simple(const struct tr_view *v){
 unsigned count=2*v->count;
 for(unsigned i=0;i<count;i++){
  dw_point a=vertex(v,i),b=vertex(v,(i+1)%count);
  if(hypotf(a.x-b.x,a.y-b.y)<1e-6f)return false;
  for(unsigned j=i+2;j<count;j++){
   if(i==0&&j==count-1)continue;
   if(overlap(a,b,vertex(v,j),vertex(v,(j+1)%count)))return false;
  }
 }
 return true;
}
static bool open_basis(const dw_world *w){
 if(w->reference_count<2||w->reference_count>TR_BASIS_POINTS||!w->reference_revision||!dw_pose_finite(w->origin))return false;
 for(unsigned i=0;i<w->reference_count;i++){
  if(!isfinite(w->reference[i].x)||!isfinite(w->reference[i].y))return false;
  if(i&&hypotf(w->reference[i].x-w->reference[i-1].x,w->reference[i].y-w->reference[i-1].y)<1e-6f)return false;
  if(i>1){dw_point a=w->reference[i-2],b=w->reference[i-1],d=w->reference[i];
   if(fabsf(orient(a,b,d))<1e-6f&&((b.x-a.x)*(d.x-b.x)+(b.y-a.y)*(d.y-b.y))<0)return false;}
  for(unsigned j=1;j+1<i;j++)if(overlap(w->reference[j-1],w->reference[j],w->reference[i-1],w->reference[i]))return false;
 }
 return !(w->reference[0].x==w->reference[w->reference_count-1].x&&w->reference[0].y==w->reference[w->reference_count-1].y);
}
bool tr_basis_compatible(const struct tr_basis *basis,const dw_world *w){
 return basis&&w&&basis->bound&&open_basis(w)&&!strcmp(basis->id,w->reference_id)&&!strcmp(basis->map,w->map_epoch)&&
  !strcmp(basis->frame,w->frame_epoch)&&!strcmp(basis->odom,w->odom_epoch)&&!strcmp(basis->configuration,w->configuration)&&
  !strcmp(basis->calibration,w->calibration)&&!strcmp(basis->primitive,w->primitive)&&!strcmp(basis->vehicle,w->vehicle)&&
  !strcmp(basis->mapper_boot,w->mapper_boot)&&!strcmp(basis->mapper_instance,w->mapper_instance)&&!strcmp(basis->clock_epoch,w->clock_epoch)&&
  w->reference_count>=basis->count&&
  !memcmp(basis->points,w->reference,basis->count*sizeof(dw_point))&&
  (w->reference_count==basis->count?w->reference_revision==basis->revision:w->reference_revision>basis->revision);
}
bool tr_basis_accept(struct tr_basis *basis,const dw_world *w){
 if(!basis||!w||!open_basis(w)||(basis->bound&&!tr_basis_compatible(basis,w)))return false;
 basis->bound=true;basis->count=w->reference_count;basis->revision=w->reference_revision;
 memcpy(basis->points,w->reference,basis->count*sizeof(dw_point));strcpy(basis->id,w->reference_id);strcpy(basis->map,w->map_epoch);
 strcpy(basis->frame,w->frame_epoch);strcpy(basis->odom,w->odom_epoch);strcpy(basis->configuration,w->configuration);
 strcpy(basis->calibration,w->calibration);strcpy(basis->primitive,w->primitive);strcpy(basis->vehicle,w->vehicle);
 strcpy(basis->mapper_boot,w->mapper_boot);strcpy(basis->mapper_instance,w->mapper_instance);strcpy(basis->clock_epoch,w->clock_epoch);return true;
}
bool tr_parking_approach(const struct tr_view *v,uint32_t id){
 if(!v||!id||v->selected!=v->from||!(v->world->required_features&DW_PARKING_MODEL))return false;
 for(unsigned i=0;i<v->world->parking_slots_count;i++){
  const xenova_drive_ParkingSlot *s=&v->world->parking_slots[i];
  if(s->slot_id==id)return s->approach_lane_id==v->selected->lane_id&&s->geometry_complete&&s->geometry_state==1;
 }
 return false;
}
static bool in_slot(const dw_point p[4],dw_point q){
 float sign=0;
 for(unsigned k=0;k<4;k++){
  float cross=orient(p[k],p[(k+1)%4],q);
  if(fabsf(cross)<1e-7f)continue;
  if(sign&&sign*cross<0)return false;
  sign=cross;
 }
 return sign!=0;
}
static bool body_inside(dw_pose center,float half,float width,dw_point p){
 float dx=p.x-center.x,dy=p.y-center.y;
 return fabsf(dx*cosf(center.yaw)+dy*sinf(center.yaw))<half-1e-6f&&
        fabsf(-dx*sinf(center.yaw)+dy*cosf(center.yaw))<width-1e-6f;
}
/* Split an exterior edge at intersections with the other region. A portion
 * inside the body is legal only when it is internal to that other region. */
static bool union_edge(const struct tr_view *v,const dw_point slot[4],dw_point a,dw_point b,
 bool road_edge,dw_pose center,float half,float width){
 float t[132]={0,1};unsigned count=2,n=road_edge?4:2*v->count;
 for(unsigned k=0;k<n;k++){
  dw_point c=road_edge?slot[k]:vertex(v,k),d=road_edge?slot[(k+1)%4]:vertex(v,(k+1)%n);
  float ax=b.x-a.x,ay=b.y-a.y,bx=d.x-c.x,by=d.y-c.y,den=ax*by-ay*bx;
  if(fabsf(den)<1e-9f)continue;
  float u=((c.x-a.x)*by-(c.y-a.y)*bx)/den,z=((c.x-a.x)*ay-(c.y-a.y)*ax)/den;
  if(u>0&&u<1&&z>=0&&z<=1)t[count++]=u;
 }
 for(unsigned k=1;k<count;k++){float value=t[k];unsigned j=k;while(j&&t[j-1]>value){t[j]=t[j-1];j--;}t[j]=value;}
 for(unsigned k=1;k<count;k++){
  float at=.5f*(t[k-1]+t[k]);dw_point q={a.x+(b.x-a.x)*at,a.y+(b.y-a.y)*at};
  if(body_inside(center,half,width,q)&&!(road_edge?in_slot(slot,q):inside(v,q)))return false;
 }
 return true;
}
bool tr_parking_contains(const struct tr_view *v,const dw_point slot[4],float slot_pad,
 dw_pose pose,float extra,const struct lp_config *c){
 if(!v||!slot||!c||v->selected!=v->from||!dw_pose_finite(pose)||!isfinite(extra)||extra<0||
    !isfinite(slot_pad)||slot_pad<0)return false;
 const xenova_drive_LaneModel *m=&v->world->lane_model;
 float radius=hypotf(fmaxf(c->front,c->rear)+c->margin,c->half_width+c->margin);
 float error=fmaxf(slot_pad,2*v->selected->position_bound_m+m->divider_position_bound_m+
  radius*(2*v->selected->heading_bound_rad+m->divider_heading_bound_rad));
 float half=.5f*(c->front+c->rear)+c->margin+extra+error,width=c->half_width+c->margin+extra+error;
 dw_pose center=pose;center.x+=.5f*(c->front-c->rear)*cosf(pose.yaw);center.y+=.5f*(c->front-c->rear)*sinf(pose.yaw);
 for(unsigned k=0;k<4;k++){
  float x=k&1?half:-half,y=k&2?width:-width;
  dw_point p={center.x+cosf(pose.yaw)*x-sinf(pose.yaw)*y,center.y+sinf(pose.yaw)*x+cosf(pose.yaw)*y};
  if(!inside(v,p)&&!in_slot(slot,p))return false;
 }
 for(unsigned k=0;k<2*v->count;k++)if(!union_edge(v,slot,vertex(v,k),vertex(v,(k+1)%(2*v->count)),true,center,half,width))return false;
 for(unsigned k=0;k<4;k++)if(!union_edge(v,slot,slot[k],slot[(k+1)%4],false,center,half,width))return false;
 return true;
}
