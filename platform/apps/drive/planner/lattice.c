/* SPDX-License-Identifier: Apache-2.0 */
#include "lattice.h"
#include <math.h>
#include <string.h>
#include <float.h>

#define PI 3.14159265358979323846f
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
 return !(flags&1)||(flags&4)||(!(flags&2)&&(double)age+c->live_age_us/1000>c->cell_age_ms)||w->occupancy.bytes[i]>=70;
}
void lp_index_build(struct lp_collision_index *index,const dw_world *w,const struct lp_config *c) {
 memset(index,0,sizeof(*index));index->world=w;index->sequence=w->sequence;
 index->cell_age_ms=c->cell_age_ms;index->live_age_us=c->live_age_us;
 for(unsigned y=0;y<w->height;y++)for(unsigned x=0;x<w->width;x++)
  if(blocked(w,(size_t)y*w->width+x,c))index->rows[y]|=UINT64_C(1)<<x;
}
enum lp_result lp_footprint(const dw_world *w,dw_pose pose,float extra,const struct lp_config *c,
 struct lp_metrics *m,const struct lp_budget *b) {
 enum lp_result rc=guard(b,m);if(rc!=LP_SEARCHING)return rc;
 if(!dw_pose_finite(pose)||!isfinite(extra)||extra<0)return LP_NUMERIC;
 if(m->cells>=c->collision_limit)return LP_WORK_BUDGET;
 m->footprints++;
 float half=(c->front+c->rear)*.5f+c->margin+extra,width=c->half_width+c->margin+extra;
 float cs=cosf(pose.yaw),sn=sinf(pose.yaw),shift=(c->front-c->rear)*.5f;
 float center_x=pose.x+shift*cs,center_y=pose.y+shift*sn;
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
 dw_pose local=dw_compose(dw_inverse(w->origin),pose);
 cs=cosf(local.yaw);sn=sinf(local.yaw);
 center_x=local.x+shift*cs;center_y=local.y+shift*sn;
 float ex=fabsf(cs)*half+fabsf(sn)*width,ey=fabsf(sn)*half+fabsf(cs)*width;
 if(!isfinite(center_x)||!isfinite(center_y)||!isfinite(ex)||!isfinite(ey)||
    center_x-ex<0||center_y-ey<0||center_x+ex>=w->width*w->resolution_m||
    center_y+ey>=w->height*w->resolution_m)return LP_COLLISION;
 int x0=(int)floorf((center_x-ex)/w->resolution_m),x1=(int)floorf((center_x+ex)/w->resolution_m);
 int y0=(int)floorf((center_y-ey)/w->resolution_m),y1=(int)floorf((center_y+ey)/w->resolution_m);
 if(x0<0||y0<0||x1>=(int)w->width||y1>=(int)w->height)return LP_COLLISION;
 float cell=w->resolution_m*.5f;
 const struct lp_collision_index *index=b->collision_index;
 bool indexed=index&&index->world==w&&index->sequence==w->sequence&&
              index->cell_age_ms==c->cell_age_ms&&index->live_age_us==c->live_age_us;
 for(int y=y0;y<=y1;y++) {
  if(++m->cells>c->collision_limit)return LP_WORK_BUDGET;
  uint64_t candidates=0;
  if(indexed) {
   uint64_t lower=~UINT64_C(0)<<x0,upper=x1==63?~UINT64_C(0):(UINT64_C(1)<<(x1+1))-1;
   candidates=index->rows[y]&lower&upper;
  }else for(int x=x0;x<=x1;x++)if(blocked(w,(size_t)y*w->width+x,c))candidates|=UINT64_C(1)<<x;
  while(candidates) {
   int x=__builtin_ctzll(candidates);candidates&=candidates-1;
   if(++m->cells>c->collision_limit)return LP_WORK_BUDGET;
   if(!(m->cells&31)){rc=guard(b,m);if(rc!=LP_SEARCHING)return rc;}
  float dx=(x+.5f)*w->resolution_m-center_x,dy=(y+.5f)*w->resolution_m-center_y;
  /* OBB/cell SAT: grid axes already overlap by the bounding range. The two
   * body axes complete the test, including obstacle cells at body corners. */
  if(fabsf(dx*cs+dy*sn)<=half+cell*(fabsf(cs)+fabsf(sn))&&
     fabsf(-dx*sn+dy*cs)<=width+cell*(fabsf(cs)+fabsf(sn)))return LP_COLLISION;
  }
 }
 return LP_PATH_FOUND;
}

static float reference_offset(const dw_world *w,dw_pose p) {
 float best=FLT_MAX,offset=0;
 for(unsigned i=1;i<w->reference_count;i++) {
  dw_point a=w->reference[i-1],b=w->reference[i];
  float dx=b.x-a.x,dy=b.y-a.y,l2=dx*dx+dy*dy;
  if(l2<1e-10f)continue;
  float t=clamp(((p.x-a.x)*dx+(p.y-a.y)*dy)/l2,0,1);
  float ex=p.x-a.x-t*dx,ey=p.y-a.y-t*dy,d=ex*ex+ey*ey;
  if(d<best){best=d;offset=(dx*ey-dy*ex)/sqrtf(l2);}
 }
 return offset;
}
enum lp_result lp_reference_goal(const dw_world *w,dw_pose pose,float horizon,float end_clearance,dw_pose *out) {
 if(!w||w->reference_count<2||!dw_pose_finite(pose)||!isfinite(horizon)||horizon<=0||!isfinite(end_clearance)||end_clearance<0)return LP_INPUT;
 float best=FLT_MAX,start=0,total=0;
 for(unsigned i=1;i<w->reference_count;i++) {
  dw_point a=w->reference[i-1],b=w->reference[i];float dx=b.x-a.x,dy=b.y-a.y,len=hypotf(dx,dy);
  if(len<1e-6f)return LP_INPUT;
  float t=clamp(((pose.x-a.x)*dx+(pose.y-a.y)*dy)/(len*len),0,1);
  float d=hypotf(pose.x-a.x-t*dx,pose.y-a.y-t*dy);
  if(d<best){best=d;start=total+t*len;}total+=len;
 }
 if(total<=end_clearance)return LP_INPUT;
 float goal=fminf(total-end_clearance,start+horizon);total=0;
 for(unsigned i=1;i<w->reference_count;i++) {
  dw_point a=w->reference[i-1],b=w->reference[i];float dx=b.x-a.x,dy=b.y-a.y,len=hypotf(dx,dy);
  if(total+len>=goal){float t=(goal-total)/len;*out=(dw_pose){a.x+t*dx,a.y+t*dy,atan2f(dy,dx)};return LP_PATH_FOUND;}
  total+=len;
 }
 return LP_INPUT;
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
         (unsigned)n->heading*83492791u^(unsigned)n->steering*2654435761u)&(LP_INDEX-1);
}
static bool key_equal(const struct lp_node *a,const struct lp_node *b) {
 return a->qx==b->qx&&a->qy==b->qy&&a->heading==b->heading&&a->steering==b->steering;
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
static bool less(const struct lp_search *s,unsigned a,unsigned b) {
 const struct lp_node *x=&s->nodes[s->heap[a]],*y=&s->nodes[s->heap[b]];
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
 if(x>=0&&y>=0&&x<(int)s->world->width&&y<(int)s->world->height) {
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
static enum lp_result build_heuristic(struct lp_search *s) {
 const dw_world *w=s->world;unsigned n=w->width*w->height;
 dw_pose local=dw_compose(dw_inverse(w->origin),s->goal);
 if(!dw_pose_finite(local)||local.x<0||local.y<0||local.x>=w->width*w->resolution_m||local.y>=w->height*w->resolution_m)return LP_INPUT;
 int gx=(int)floorf(local.x/w->resolution_m),gy=(int)floorf(local.y/w->resolution_m);
 if(gx<0||gy<0||gx>=(int)w->width||gy>=(int)w->height)return LP_INPUT;
 for(unsigned k=0;k<n;k++){s->nodes[k].g=s->nodes[k].f=FLT_MAX;s->nodes[k].heap=-1;s->nodes[k].closed=0;}
 int goal=gy*(int)w->width+gx;s->nodes[goal].g=s->nodes[goal].f=0;push(s,goal);
 while(s->open) {
  enum lp_result rc=guard(&s->budget,&s->metrics);if(rc!=LP_SEARCHING)return rc;
  int at=pop(s);s->nodes[at].closed=1;s->heuristic_expanded++;
  int x=at%(int)w->width,y=at/(int)w->width;
  for(int dy=-1;dy<=1;dy++)for(int dx=-1;dx<=1;dx++) {
   if((!dx&&!dy)||heuristic_blocked(s,x+dx,y+dy))continue;
   int id=(y+dy)*(int)w->width+x+dx;
   if(s->nodes[id].closed)continue;
   float next=s->nodes[at].g+w->resolution_m*(dx&&dy?1.41421356237f:1);
   if(next>=s->nodes[id].g)continue;
   s->nodes[id].g=s->nodes[id].f=next;
   if(s->nodes[id].heap<0)push(s,id);else up(s,(unsigned)s->nodes[id].heap);
  }
 }
 for(unsigned k=0;k<n;k++)s->heuristic[k]=s->nodes[k].g;
 s->heuristic_open_high_water=s->metrics.open_high_water;s->metrics.open_high_water=0;
 memset(&s->nodes[0],0,sizeof(s->nodes[0]));return LP_SEARCHING;
}
static bool reached(const struct lp_search *s,const struct lp_node *n) {
 if(fabsf(dw_angle(n->pose.yaw-s->goal.yaw))>s->config->goal_heading)return false;
 if(n->parent<0&&distance(n->pose,s->goal)<=s->config->goal_distance)return true;
 if(fabsf(n->steer)>1e-5f)return false;
 dw_pose local=dw_compose(dw_inverse(n->pose),s->goal);
 /* A lateral bypass needs its settling segment. An ordinary small correction
  * near the reference does not need to drive away to manufacture that length.
  * Use the same resolution threshold as the path's maneuver classification. */
 float minimum=n->max_offset>s->config->resolution?s->config->goal_straight_m:0;
 return local.x>=minimum&&
        local.x<=s->config->goal_straight_m+s->config->primitive_speed*s->config->primitive_duration&&
        fabsf(local.y)<=s->config->goal_distance;
}
enum lp_result lp_start(struct lp_search *s,const dw_world *w,dw_pose pose,float steer,dw_pose goal,
 const struct lp_config *c,const struct lp_assets *a,struct lp_budget budget,int preferred) {
 if(!s||!w||!c||!a||!dw_pose_finite(pose)||!dw_pose_finite(goal)||!isfinite(steer)||
    !c->node_limit||c->node_limit>LP_NODES||!c->expansion_limit||!c->collision_limit||c->steering_levels!=LP_STEERS||
    strcmp(c->id,w->configuration)||strcmp(a->sha256,w->primitive)||strcmp(c->id,a->id)||
    fabsf(c->resolution-w->resolution_m)>1e-6f||fabsf(c->resolution-a->resolution)>1e-6f||
    fabsf(c->wheelbase-a->wheelbase)>1e-6f||fabsf(c->max_steer-a->max_steer)>1e-6f||
    fabsf(c->steer_rate-a->steer_rate)>1e-6f||fabsf(c->primitive_duration-a->duration)>1e-6f||
    fabsf(c->primitive_speed-a->speed)>1e-6f||fabsf(steer)>c->max_steer)return LP_INPUT;
 memset(s,0,sizeof(*s));s->world=w;s->config=c;s->assets=a;s->budget=budget;s->start=pose;s->goal=goal;
 if(!budget.collision_index){lp_index_build(&s->collision_index,w,c);s->budget.collision_index=&s->collision_index;}
 s->initial_steer=steer;s->preferred_direction=preferred;s->result=LP_SEARCHING;
 for(unsigned k=0;k<LP_INDEX;k++)s->index[k]=-1;
 enum lp_result rc=lp_footprint(w,pose,c->tracking_guard,c,&s->metrics,&s->budget);
 if(rc!=LP_PATH_FOUND){s->result=rc;return rc;}
 rc=build_heuristic(s);if(rc!=LP_SEARCHING)return s->result=rc;
 struct lp_node *root=&s->nodes[0];root->pose=pose;root->steer=steer;root->parent=-1;root->heap=-1;
 root->max_offset=fabsf(reference_offset(w,pose));
 root->steering=(uint8_t)clamp(roundf(steer/c->max_steer*(LP_STEERS/2))+(LP_STEERS/2),0,LP_STEERS-1);root->f=heuristic(s,pose);
 quantize(s,root);bool exists;int slot=index_slot(s,root,&exists);
 if(slot<0)return LP_NODE_BUDGET;
 s->index[slot]=0;s->used=1;s->metrics.nodes_high_water=1;push(s,0);return LP_SEARCHING;
}
/* All ordinary edges use the generated control set. A measured root steering
 * between levels is connected by the same continuous ramp integration; it is
 * never snapped to a nominal initial curvature. */
static enum lp_result edge(struct lp_search *s,int parent,unsigned target,struct lp_node *next,
 xenova_drive_PathPoint *points,size_t *count) {
 const struct lp_node *old=&s->nodes[parent];const struct lp_config *c=s->config;
 const struct lp_primitive_point *samples=s->assets->motion[old->steering][target];
 float initial=c->max_steer*((int)old->steering-LP_STEERS/2)/(LP_STEERS/2);
 float final=c->max_steer*((int)target-LP_STEERS/2)/(LP_STEERS/2);
 float dt=c->primitive_duration/(LP_PRIMITIVE_SAMPLES-1);
 if(fabsf(final-old->steer)/c->primitive_duration>c->steer_rate+1e-5f)return LP_COLLISION;
 bool custom=fabsf(old->steer-initial)>1e-5f;dw_pose pose=old->pose;
 float radius=hypotf(fmaxf(c->front,c->rear)+c->margin,c->half_width+c->margin);
 float yaw_step=c->primitive_speed/c->wheelbase*tanf(c->max_steer)*dt;
 float padding=.5f*(c->primitive_speed*dt+radius*fabsf(yaw_step));
 enum lp_result first=lp_footprint(s->world,pose,padding+c->tracking_guard,c,&s->metrics,&s->budget);
 if(first!=LP_PATH_FOUND)return first;
 for(unsigned k=1;k<LP_PRIMITIVE_SAMPLES;k++) {
  enum lp_result rc=guard(&s->budget,&s->metrics);if(rc!=LP_SEARCHING)return rc;
  float steering=old->steer+(final-old->steer)*k/(LP_PRIMITIVE_SAMPLES-1);
  if(custom)integrate(&pose,old->steer+(final-old->steer)*(k-.5f)/(LP_PRIMITIVE_SAMPLES-1),c->primitive_speed,dt,c->wheelbase);
  else pose=dw_compose(old->pose,(dw_pose){samples[k].x,samples[k].y,samples[k].yaw});
  /* Half the maximum translational+corner travel bounds the gap to an
   * adjacent sample. Both endpoint boxes cover that swept interval. */
  rc=lp_footprint(s->world,pose,padding+c->tracking_guard,c,&s->metrics,&s->budget);
  if(rc!=LP_PATH_FOUND)return rc;
  if(points){if(*count>=LP_PATH)return LP_PATH_LIMIT;points[(*count)++]=(xenova_drive_PathPoint){.pose=pose,.steering_rad=steering,.velocity_mps=c->primitive_speed};}
 }
 next->pose=pose;next->steer=final;next->steering=(uint8_t)target;return LP_PATH_FOUND;
}
static enum lp_result reconstruct(struct lp_search *s,int last,struct lp_path *path) {
 /* A failed terminal connection must leave the open heap intact. At most 31
  * ordinary edges fit in the 512-point result, so this chain is 128 bytes. */
 int chain[LP_PATH/(LP_PRIMITIVE_SAMPLES-1)];
 unsigned n=0;int at=last;
 while(s->nodes[at].parent>=0){if(n>=sizeof(chain)/sizeof(*chain))return LP_PATH_LIMIT;chain[n++]=at;at=s->nodes[at].parent;}
 memset(path,0,sizeof(*path));path->count=1;path->goal=s->goal;
 path->points[0]=(xenova_drive_PathPoint){.pose=s->start,.steering_rad=s->initial_steer,.velocity_mps=s->config->primitive_speed};
 for(unsigned k=n;k>0;k--) {
  int node=chain[k-1];struct lp_node ignored={0};
  enum lp_result rc=edge(s,s->nodes[node].parent,s->nodes[node].edge,&ignored,path->points,&path->count);
  if(rc!=LP_PATH_FOUND)return rc;
 }
 dw_pose end=s->nodes[last].pose,local=dw_compose(dw_inverse(end),s->goal);
 bool stationary=distance(end,s->goal)<=s->config->goal_distance;
 if(stationary&&!n)path->points[0].velocity_mps=0;
 float maximum_step=s->config->primitive_speed*s->config->primitive_duration/(LP_PRIMITIVE_SAMPLES-1);
 unsigned steps=stationary?1:(unsigned)ceilf(local.x/maximum_step);
 if(!steps||steps>LP_PATH-path->count)return LP_PATH_LIMIT;
 float ds=stationary?0:local.x/steps,padding=ds*.5f+s->config->tracking_guard;
 for(unsigned k=0;k<=steps;k++) {
  dw_pose pose={end.x+cosf(end.yaw)*ds*k,end.y+sinf(end.yaw)*ds*k,end.yaw};
  enum lp_result rc=lp_footprint(s->world,pose,padding,s->config,&s->metrics,&s->budget);
  if(rc!=LP_PATH_FOUND)return rc;
  if(k)path->points[path->count++]=(xenova_drive_PathPoint){.pose=pose,
   .steering_rad=stationary?s->nodes[last].steer:0,.velocity_mps=stationary?0:s->config->primitive_speed};
 }
 strcpy(path->frame,s->world->frame_epoch);strcpy(path->odom,s->world->odom_epoch);
 strcpy(path->configuration,s->world->configuration);strcpy(path->reference,s->world->reference_id);
 float furthest=0;
 for(size_t k=0;k<path->count;k++){float offset=reference_offset(s->world,path->points[k].pose);if(fabsf(offset)>fabsf(furthest))furthest=offset;}
 path->direction=fabsf(furthest)>s->config->resolution?(furthest>0?1:-1):0;
 path->phase=path->direction?1:0;path->revision=path->maneuver_revision=1;
 return LP_PATH_FOUND;
}
enum lp_result lp_advance(struct lp_search *s,unsigned expansions,struct lp_path *path) {
 if(s->result!=LP_SEARCHING)return s->result;
 if(!expansions||expansions>32)return LP_INPUT;
 for(unsigned step=0;step<expansions;step++) {
  enum lp_result rc=guard(&s->budget,&s->metrics);if(rc!=LP_SEARCHING)return s->result=rc;
  if(!s->open)return s->result=LP_NOT_FOUND;
  if(s->metrics.expanded>=s->config->expansion_limit)return s->result=LP_WORK_BUDGET;
  int current=pop(s);struct lp_node *node=&s->nodes[current];node->closed=1;s->metrics.expanded++;
  if(reached(s,node)) {
   rc=reconstruct(s,current,path);
   if(rc!=LP_COLLISION)return s->result=rc;
   path->count=0;
  }
  for(unsigned target=0;target<LP_STEERS;target++) {
   /* A virtual root has the finer connection set for measured steering.
    * Interior expansion uses the coarser regular lattice, avoiding duplicate
    * near-equivalent curvature branches throughout the whole search graph. */
   if(current!=0&&(target&1))continue;
   struct lp_node next={.parent=current,.heap=-1,.edge=(uint8_t)target};s->metrics.primitives++;
   rc=edge(s,current,target,&next,NULL,NULL);
   if(rc==LP_COLLISION)continue;
   if(rc!=LP_PATH_FOUND)return s->result=rc;
   quantize(s,&next);float offset=reference_offset(s->world,next.pose);
   next.max_offset=fmaxf(node->max_offset,fabsf(offset));
   float change=s->preferred_direction&&offset*s->preferred_direction< -s->config->resolution?s->config->switch_cost:0;
   next.g=node->g+s->config->primitive_speed*s->config->primitive_duration+
          s->config->deviation_cost*offset*offset+s->config->curvature_cost*fabsf(next.steer)+change;
   next.f=next.g+heuristic(s,next.pose);
   if(!isfinite(next.f))return s->result=LP_NUMERIC;
   bool exists;int slot=index_slot(s,&next,&exists);
   if(slot<0)return s->result=LP_NODE_BUDGET;
   if(exists) {
    int id=s->index[slot];struct lp_node *prior=&s->nodes[id];
    if(prior->g<=next.g+1e-6f)continue;
    next.heap=prior->heap;*prior=next;
    if(prior->heap<0)push(s,id);else up(s,(unsigned)prior->heap);
   }else {
    if(s->used>=s->config->node_limit)return s->result=LP_NODE_BUDGET;
    int id=(int)s->used++;s->nodes[id]=next;s->index[slot]=id;push(s,id);
    if(s->used>s->metrics.nodes_high_water)s->metrics.nodes_high_water=s->used;
   }
  }
 }
 return LP_SEARCHING;
}
enum lp_result lp_validate_path(const struct lp_path *p,const dw_world *w,const struct lp_config *c,
 struct lp_metrics *m,const struct lp_budget *b) {
 if(p->count<2||p->count>LP_PATH||strcmp(p->frame,w->frame_epoch)||strcmp(p->odom,w->odom_epoch)||
    strcmp(p->configuration,w->configuration)||strcmp(p->reference,w->reference_id))return LP_INPUT;
 for(size_t k=0;k<p->count;k++) {
  const xenova_drive_PathPoint *point=&p->points[k];
  if(!dw_pose_finite(point->pose)||!isfinite(point->steering_rad)||fabsf(point->steering_rad)>c->max_steer+1e-5f||
     !isfinite(point->velocity_mps)||point->velocity_mps<0||point->velocity_mps>c->max_velocity)return LP_INPUT;
  float pad=0;
  if(!k&&p->count>1) {
   float ds=distance(p->points[0].pose,p->points[1].pose),dy=fabsf(dw_angle(p->points[1].pose.yaw-p->points[0].pose.yaw));
   pad=.5f*(ds+hypotf(c->front,c->half_width)*dy);
  }
  if(k){float ds=distance(point->pose,p->points[k-1].pose),dy=fabsf(dw_angle(point->pose.yaw-p->points[k-1].pose.yaw));
   if(ds>c->primitive_speed*c->primitive_duration/16*1.05f||dy>ds*tanf(c->max_steer)/c->wheelbase*1.1f+.0001f)return LP_INPUT;
   float dt=ds/c->primitive_speed;
   if(fabsf(point->steering_rad-p->points[k-1].steering_rad)>c->steer_rate*dt+.0001f)return LP_INPUT;
   dw_pose predicted=p->points[k-1].pose;
   integrate(&predicted,.5f*(point->steering_rad+p->points[k-1].steering_rad),c->primitive_speed,dt,c->wheelbase);
   if(distance(predicted,point->pose)>.001f||fabsf(dw_angle(predicted.yaw-point->pose.yaw))>.001f)return LP_INPUT;
   pad=.5f*(ds+hypotf(c->front,c->half_width)*dy);
  }
  enum lp_result rc=lp_footprint(w,point->pose,pad+c->tracking_guard,c,m,b);if(rc!=LP_PATH_FOUND)return rc;
 }
 return LP_PATH_FOUND;
}

/* Forward projection uses the final limited steering/velocity, actual current
 * state, an explicit reaction interval and braking. It never reuses the
 * planner's valid bit as a command-collision certificate. */
static enum lp_result project(const dw_world *w,dw_pose pose,float current_velocity,float current_steer,
 float velocity,float steer,float uncertainty,float steering_error,const struct lp_config *c,struct lp_metrics *m,const struct lp_budget *b,float *known) {
 float dt=fminf(.025f,c->resolution/(4*fmaxf(c->max_velocity,.01f)));
 float t=0,v=fmaxf(0,current_velocity),delta=current_steer;*known=0;
 float horizon=c->reaction_s+fmaxf(v,velocity)/c->max_brake+.1f;
 float yaw_error=c->max_velocity/c->wheelbase*horizon*
  fmaxf(tanf(fabsf(current_steer)+steering_error)-tanf(fabsf(current_steer)),
        tanf(fabsf(steer)+steering_error)-tanf(fabsf(steer)));
 unsigned steps=(unsigned)ceilf(horizon/dt);
 if(steps>512)return LP_WORK_BUDGET;
 for(unsigned k=0;k<=steps;k++) {
  float extra=.5f*(fmaxf(v,velocity)*dt+hypotf(c->front,c->half_width)*
      c->max_velocity/c->wheelbase*fabsf(tanf(c->max_steer))*dt);
  float envelope=(c->max_velocity*t+hypotf(c->front,c->half_width))*yaw_error;
  enum lp_result rc=lp_footprint(w,pose,extra+uncertainty+envelope,c,m,b);if(rc!=LP_PATH_FOUND)return rc;
  delta+=clamp(steer-delta,-c->steer_rate*dt,c->steer_rate*dt);
  if(t<c->reaction_s)v+=clamp(velocity-v,-c->max_brake*dt,c->max_accel*dt);
  else v=fmaxf(0,v-c->max_brake*dt);
  integrate(&pose,delta,v,dt,c->wheelbase);*known+=v*dt;t+=dt;
 }
 return LP_PATH_FOUND;
}
enum lp_result lp_track(struct lp_tracker *tracker,const struct lp_path *path,const dw_world *w,dw_pose pose,
 const dw_vehicle *vehicle,const struct lp_config *c,float dt,struct lp_command *out,
 struct lp_metrics *m,const struct lp_budget *b) {
 memset(out,0,sizeof(*out));out->result=LP_INPUT;
 if(!dw_pose_finite(pose)||!isfinite(dt)||dt<=0||dt>.1f||path->count<2||path->count>LP_PATH||
    !isfinite(vehicle->velocity_mps)||vehicle->velocity_mps< -.01f||vehicle->velocity_mps>c->max_velocity+.01f||
    !isfinite(vehicle->position_error_m)||vehicle->position_error_m<0||vehicle->position_error_m>c->max_position_error||
    !isfinite(vehicle->heading_error_rad)||vehicle->heading_error_rad<0||vehicle->heading_error_rad>c->max_heading_error||
    !vehicle->has_steering_error_rad||!isfinite(vehicle->steering_error_rad)||vehicle->steering_error_rad<0||
    vehicle->steering_error_rad>c->max_steering_error||
    vehicle->quality!=3||strcmp(path->frame,w->frame_epoch)||strcmp(path->odom,w->odom_epoch)||
    strcmp(path->configuration,w->configuration)||strcmp(path->reference,w->reference_id))return LP_INPUT;
 float actual_steer=vehicle->has_measured_steering_rad?vehicle->measured_steering_rad:
                    vehicle->has_estimated_steering_rad?vehicle->estimated_steering_rad:NAN;
 if(!isfinite(actual_steer)||fabsf(actual_steer)+vehicle->steering_error_rad>c->max_steer+.0001f)return LP_INPUT;
 float uncertainty=vehicle->position_error_m+hypotf(c->front,c->half_width)*vehicle->heading_error_rad;
 float best=FLT_MAX;size_t nearest=0;
 for(size_t k=0;k<path->count;k++){float d=distance(pose,path->points[k].pose);if(d<best){best=d;nearest=k;}}
 if(best>c->tracking_error)return LP_INPUT;
 tracker->index=nearest;
 if(path->direction){
  float peak=0;size_t peak_index=0;
  for(size_t k=0;k<path->count;k++){float offset=fabsf(reference_offset(w,path->points[k].pose));if(offset>peak){peak=offset;peak_index=k;}}
  out->maneuver_phase=path->phase==2||nearest>peak_index?2:1;
 }
 float remaining=distance(pose,path->points[nearest].pose);
 for(size_t k=nearest+1;k<path->count;k++)remaining+=distance(path->points[k-1].pose,path->points[k].pose);
 if(distance(pose,path->goal)<=c->goal_distance&&fabsf(dw_angle(pose.yaw-path->goal.yaw))<=c->goal_heading) {
  float known;enum lp_result stopped=project(w,pose,vehicle->velocity_mps,actual_steer,0,actual_steer,uncertainty,vehicle->steering_error_rad,c,m,b,&known);
  out->steer=actual_steer;out->emergency=true;out->result=stopped==LP_PATH_FOUND?LP_STOP:stopped;
  if(stopped==LP_PATH_FOUND)tracker->completed++;
  return out->result;
 }
 float lookahead=clamp(fabsf(vehicle->velocity_mps)*c->lookahead_time,c->lookahead_min,c->lookahead_max);
 size_t target=nearest;float along=0;
 while(target+1<path->count&&along<lookahead){along+=distance(path->points[target].pose,path->points[target+1].pose);target++;}
 dw_pose local=dw_compose(dw_inverse(pose),path->points[target].pose);
 float d2=local.x*local.x+local.y*local.y;
 if(d2<1e-8f||local.x<=0)return LP_INPUT;
 out->target_x=local.x;out->target_y=local.y;out->geometric_distance=sqrtf(d2);
 out->curvature=2*local.y/d2;
 float desired=atanf(c->wheelbase*out->curvature);
 float allowed=c->max_steer-vehicle->steering_error_rad;
 float delta=clamp(desired,-allowed,allowed);
 delta=actual_steer+clamp(delta-actual_steer,-c->steer_rate*dt,c->steer_rate*dt);
 float speed=fminf(c->max_velocity,path->points[target].velocity_mps);
 if(fabsf(out->curvature)>1e-6f)speed=fminf(speed,sqrtf(c->max_lateral_accel/fabsf(out->curvature)));
 speed=fminf(speed,sqrtf(2*c->max_brake*fmaxf(0,remaining)));
 /* Independent metric clearance: forward final-curvature probes, not a
  * planner cost->metres conversion. Bound candidate speed by a braking model. */
 float known=0;enum lp_result rc=project(w,pose,vehicle->velocity_mps,actual_steer,c->max_velocity,delta,uncertainty,vehicle->steering_error_rad,c,m,b,&known);
 if(rc!=LP_PATH_FOUND&&rc!=LP_COLLISION)return out->result=rc;
 out->known_distance=known;
 if(rc==LP_COLLISION) {
  float safe=fmaxf(0,known-c->margin-vehicle->position_error_m);
  float limit=sqrtf(c->max_brake*c->max_brake*c->reaction_s*c->reaction_s+2*c->max_brake*safe)-c->max_brake*c->reaction_s;
  speed=fminf(speed,limit);
 }
 float limited=vehicle->velocity_mps+clamp(speed-vehicle->velocity_mps,-c->max_brake*dt,c->max_accel*dt);
 if(speed<vehicle->velocity_mps-c->max_brake*dt){limited=0;out->emergency=true;}
 rc=project(w,pose,vehicle->velocity_mps,actual_steer,limited,delta,uncertainty,vehicle->steering_error_rad,c,m,b,&known);
 if(rc!=LP_PATH_FOUND){out->velocity=0;out->steer=actual_steer;out->emergency=true;return out->result=rc;}
 out->velocity=limited;out->steer=delta;out->result=LP_PATH_FOUND;
 tracker->last_velocity=limited;tracker->last_steer=delta;tracker->completed++;
 return LP_PATH_FOUND;
}
const char *lp_reason(enum lp_result result) {
 static const char *const names[]={"SEARCHING","PATH_FOUND","NORMAL_STOP","SEARCH_NOT_FOUND","NODE_BUDGET",
  "WORK_BUDGET","DEADLINE","CANCELLED","INPUT_INVALID","NUMERIC_ERROR","COLLISION","PATH_LIMIT","CLOCK_ERROR"};
 return (unsigned)result<sizeof(names)/sizeof(*names)?names[result]:"INTERNAL_ERROR";
}
