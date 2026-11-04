/* SPDX-License-Identifier: Apache-2.0 */
#include "engine.h"
#include <string.h>
#include <math.h>

static bool take(struct drive_engine *e){
 if(!atomic_flag_test_and_set_explicit(&e->lock,memory_order_acquire))return true;
 atomic_fetch_add(&e->resource_contention,1);return false;
}
static void give(struct drive_engine *e){atomic_flag_clear_explicit(&e->lock,memory_order_release);}
static bool readers(const struct de_path_slot *s){for(unsigned i=0;i<DE_READERS;i++)if(s->leases[i])return true;return false;}
void de_init(struct drive_engine *e,const struct lp_config *c,const struct lp_assets *a){
 memset(e,0,sizeof(*e));atomic_flag_clear(&e->lock);dw_pool_init(&e->maps);dw_odom_init(&e->odometry);
 e->config=c;e->assets=a;e->current=-1;e->epoch=1;e->last_reason=DE_WAITING;
}
static enum de_result reserve(struct drive_engine *e,struct de_reference *h,struct lp_path **path){
 if(!take(e))return DE_BUSY;
 enum de_result rc=DE_BUSY;
 if(e->stopping)rc=DE_CANCELLED;
 else if(e->next_generation==UINT32_MAX)rc=DE_LIMIT;
 else for(unsigned i=0;i<DE_PATH_SLOTS;i++)if((int)i!=e->current&&!readers(&e->paths[i])&&!e->paths[i].writing){
  struct de_path_slot *slot=&e->paths[i];slot->writing=true;slot->generation=++e->next_generation;
  *h=(struct de_reference){i,slot->generation,0};*path=&slot->path;rc=DE_OK;
  unsigned used=0;
  for(unsigned j=0;j<DE_PATH_SLOTS;j++)if((int)j==e->current||e->paths[j].writing||readers(&e->paths[j]))used++;
  if(used>e->path_peak)e->path_peak=used;
  break;
 }
 if(rc==DE_BUSY)e->path_exhausted++;
 give(e);return rc;
}
static enum de_result publish(struct drive_engine *e,struct de_reference h,bool valid){
 if(!take(e))return DE_BUSY;
 enum de_result rc=DE_CHANGED;
 if(h.slot<DE_PATH_SLOTS&&e->paths[h.slot].generation==h.generation&&e->paths[h.slot].writing){
  if(valid&&!e->stopping){e->current=(int)h.slot;e->path_invalidated=false;rc=DE_OK;}
  e->paths[h.slot].writing=false;
 }
 give(e);return rc;
}
enum de_result de_path_acquire(struct drive_engine *e,struct de_reference *h,const struct lp_path **out){
 if(!take(e))return DE_BUSY;
 enum de_result rc=DE_WAITING;
 if(e->current>=0&&!e->stopping){
  struct de_path_slot *s=&e->paths[e->current];
  /* Fixed product read owners, each releases before its next acquire. */
  rc=DE_BUSY;
  for(unsigned k=0;k<DE_READERS;k++)if(!s->leases[k]&&e->next_lease<UINT32_MAX){
   s->leases[k]=++e->next_lease;*h=(struct de_reference){(unsigned)e->current,s->generation,e->next_lease};*out=&s->path;rc=DE_OK;break;
  }
 }
 give(e);return rc;
}
enum de_result de_path_release(struct drive_engine *e,struct de_reference h){
 if(h.slot<DE_PATH_SLOTS&&e->paths[h.slot].generation==h.generation&&h.lease){
  for(unsigned k=0;k<DE_READERS;k++){unsigned expected=h.lease;
   if(atomic_compare_exchange_strong(&e->paths[h.slot].leases[k],&expected,0))return DE_OK;
  }
 }
 return DE_CHANGED;
}
enum de_result de_world(struct drive_engine *e,const uint8_t *bytes,size_t size,uint64_t now){
 if(de_receive_drain(e))return DE_BUSY;
 struct dw_handle h;struct dw_snapshot *snapshot;
 enum dw_result rc=dw_reserve(&e->maps,&h,&snapshot);if(rc)return rc==DW_BUSY?DE_BUSY:DE_MAP;
 /* The receive owner also owns trust updates, so the trust is stable during
  * decode. source_revision still guards the final publication. */
 rc=dw_decode(bytes,size,&e->maps.trust,now,snapshot);
 e->last_world_result=rc;
 if(rc){
  if(dw_abort(&e->maps,h)==DW_BUSY){e->receive_pending=true;e->receive_handle=h;}
  return rc==DW_BUSY?DE_BUSY:DE_MAP;
 }
 e->receive_awaiting=true;e->receive_handle=h;e->receive_snapshot=snapshot;
 (void)de_receive_poll(e,now);
 return DE_OK;
}
enum de_result de_receive_drain(struct drive_engine *e){
 if(!e->receive_pending&&!e->receive_awaiting)return DE_OK;
 if(dw_abort(&e->maps,e->receive_handle)==DW_BUSY)return DE_BUSY;
 e->receive_pending=false;e->receive_awaiting=false;return DE_OK;
}
enum de_result de_vehicle(struct drive_engine *e,const dw_vehicle *vehicle){
 enum dw_result rc=dw_odom_add(&e->odometry,vehicle);
 return !rc?DE_OK:rc==DW_BUSY?DE_BUSY:DE_POSE;
}
static enum de_result inputs(struct drive_engine *e,const struct dw_snapshot *map,uint64_t now,dw_pose *pose,dw_vehicle *vehicle,uint64_t *deadline){
 if(dw_snapshot_time(map,now,(uint64_t)e->config->live_age_us,deadline))return DE_EXPIRED;
 enum dw_result rc=dw_current_pose(&map->world,&e->odometry,now,(uint64_t)e->config->pose_age_us,pose,vehicle);
 if(rc)return rc==DW_BUSY?DE_BUSY:DE_POSE;
 if(vehicle->position_error_m>e->config->max_position_error||vehicle->heading_error_rad>e->config->max_heading_error)return DE_POSE;
 return DE_OK;
}
enum de_result de_receive_poll(struct drive_engine *e,uint64_t now){
 if(e->receive_pending)return de_receive_drain(e);
 if(!e->receive_awaiting)return DE_OK;
 dw_pose pose;dw_vehicle vehicle;
 enum de_result rc=inputs(e,e->receive_snapshot,now,&pose,&vehicle,NULL);
 if(rc==DE_EXPIRED||e->stopping){(void)de_receive_drain(e);return rc;}
 if(rc)return rc;
 enum dw_result published=dw_publish(&e->maps,e->receive_handle);
 e->last_world_result=published;
 if(published==DW_BUSY)return DE_BUSY;
 if(published){(void)de_receive_drain(e);return DE_MAP;}
 e->receive_awaiting=false;
 if(atomic_load(&e->requested)==UINT32_MAX)return DE_LIMIT;
 atomic_fetch_add(&e->requested,1);return DE_OK;
}
static void search_memory_publish(struct drive_engine *e){
 unsigned used=e->search.used;
 if(used>atomic_load(&e->search_peak))atomic_store(&e->search_peak,used);
 atomic_store(&e->search_used,used);
}
enum de_result de_plan_start(struct drive_engine *e,uint64_t now,struct lp_budget budget){
 if(e->planning||e->apply_pending)return DE_BUSY;
 bool stopped=e->stopping;uint32_t request=e->requested;
 if(stopped)return DE_CANCELLED;
 if(!request||request==e->started)return DE_WAITING;
 struct dw_handle h;const struct dw_snapshot *map;
 enum dw_result dr=dw_acquire(&e->maps,&h,&map);if(dr)return DE_MAP;
 dw_pose pose,goal;dw_vehicle vehicle={0};enum de_result rc=inputs(e,map,now,&pose,&vehicle,NULL);
 float terminal_sweep=.5f*e->config->primitive_speed*e->config->primitive_duration/(LP_PRIMITIVE_SAMPLES-1);
 /* The terminal node may retain a valid nonzero heading. A front-only
  * clearance misses the rotated outer corner; reserve its circumscribed radius. */
 float end_clearance=hypotf(e->config->front+e->config->margin+e->config->tracking_guard+terminal_sweep,
                           e->config->half_width+e->config->margin+e->config->tracking_guard+terminal_sweep);
 if(!rc&&lp_reference_goal(&map->world,pose,e->config->plan_horizon,
    end_clearance,&goal)!=LP_PATH_FOUND)rc=DE_PATH;
 float speed=fmaxf(0,vehicle.velocity_mps);
 float replan_distance=fmaxf(e->config->lookahead_min,fminf(e->config->lookahead_max,speed*e->config->lookahead_time))+
                       speed*e->config->reaction_s+speed*speed/(2*e->config->max_brake)+e->config->goal_distance;
 if(!rc&&e->maneuver_active){
  struct lp_road_state current,merge;float steering=vehicle.has_measured_steering_rad?vehicle.measured_steering_rad:vehicle.estimated_steering_rad;
  bool projected=lp_reference_project(&map->world,pose,&current)==LP_PATH_FOUND&&
                 lp_reference_project(&map->world,e->maneuver_goal,&merge)==LP_PATH_FOUND;
  bool member=projected&&hypotf(merge.pose.x-e->maneuver_goal.x,merge.pose.y-e->maneuver_goal.y)<=e->config->goal_distance;
  if(e->merge_passed_reference_revision!=map->world.reference_revision)e->merge_passed_reference_revision=0;
  if(member&&current.arc-vehicle.position_error_m>=merge.arc)e->merge_passed_reference_revision=map->world.reference_revision;
  bool rejoined=projected&&e->merge_passed_reference_revision&&
                fabsf(current.offset)+vehicle.position_error_m<=e->config->goal_distance&&
                fabsf(dw_angle(pose.yaw-current.pose.yaw))+vehicle.heading_error_rad<=e->config->goal_heading&&
                fabsf(steering-atanf(e->config->wheelbase*current.curvature))+vehicle.steering_error_rad<=e->config->max_steer/(LP_STEERS/2);
  if(strcmp(e->maneuver_frame,map->world.frame_epoch)||strcmp(e->maneuver_odom,map->world.odom_epoch)||
   strcmp(e->maneuver_configuration,map->world.configuration)||strcmp(e->maneuver_reference,map->world.reference_id)||
   rejoined){
   e->maneuver_active=false;e->maneuver_direction=0;e->merge_passed_reference_revision=0;
   if(e->maneuver_revision<UINT64_MAX)e->maneuver_revision++;
  }else if(member&&!e->merge_passed_reference_revision&&merge.arc-current.arc>replan_distance)goal=e->maneuver_goal;
  /* Keep the merge milestone while a fresh local path continues beyond it.
   * Reaching a sampled endpoint is not proof of physical road rejoining. */
 }
 if(!rc){
  struct de_reference previous;const struct lp_path *old;
  if(!de_path_acquire(e,&previous,&old)){
   struct lp_metrics checked={0};
   bool same_intent=(old->direction!=0)==e->maneuver_active&&
                    old->merge_passed_reference_revision==e->merge_passed_reference_revision;
   float remaining=lp_path_remaining(old,pose,NULL);
   bool same_goal=hypotf(goal.x-old->goal.x,goal.y-old->goal.y)<e->config->goal_distance||remaining>replan_distance;
   enum lp_result usable=same_intent&&same_goal?lp_validate_path(old,&map->world,e->config,&checked,&budget):LP_INPUT;
   if(same_intent&&same_goal&&(usable==LP_COLLISION||usable==LP_INPUT||usable==LP_NUMERIC))e->path_invalidated=true;
   (void)de_path_release(e,previous);
   if(usable==LP_PATH_FOUND){e->path_invalidated=false;e->started=request;(void)dw_release(&e->maps,h);return DE_OK;}
  }
 }
 struct lp_path *path=NULL;
 if(!rc)rc=reserve(e,&e->planning_path,&path);
 if(rc){(void)dw_release(&e->maps,h);return e->last_reason=rc;}
 e->planning_map=h;e->planning_snapshot=map;e->planning=true;e->plan_finalized=false;
 e->started=request;e->budget=budget;e->plans++;
 float steering=vehicle.has_measured_steering_rad?vehicle.measured_steering_rad:
                 vehicle.has_estimated_steering_rad?vehicle.estimated_steering_rad:NAN;
 e->search_reason=lp_start_local(&e->search,&map->world,pose,steering,goal,e->config,e->assets,budget,e->maneuver_direction);
 search_memory_publish(e);
 return e->last_reason=DE_OK;
}
enum de_result de_plan_slice(struct drive_engine *e,unsigned expansions,uint64_t now){
 if(!e->planning)return DE_WAITING;
 if(e->stopping)e->search_reason=LP_CANCELLED;
 struct lp_path *path=&e->paths[e->planning_path.slot].path;
 if(e->search_reason==LP_SEARCHING)e->search_reason=lp_advance(&e->search,expansions,path);
 search_memory_publish(e);
 if(e->search_reason==LP_SEARCHING)return DE_WAITING;
 /* A high-rate pose-anchored map must not starve a bounded search. Validate
  * the completed path against the latest complete map, keeping the original
  * search deadline. Every later command still projects against its own map. */
 if(!e->plan_finalized){
  bool valid=e->search_reason==LP_PATH_FOUND&&!e->stopping;
  if(valid){
   struct dw_handle current;const struct dw_snapshot *snapshot;
   enum dw_result acquired=dw_acquire(&e->maps,&current,&snapshot);
   if(acquired==DW_BUSY)return DE_BUSY;
   if(acquired)valid=false;
   else{
    if(current.generation!=e->planning_map.generation){
     struct lp_metrics checked={0};enum lp_result result=lp_validate_path(path,&snapshot->world,e->config,&checked,&e->budget);
     if(result!=LP_PATH_FOUND){e->search_reason=result;valid=false;}
    }
    if(valid){
     dw_pose pose;dw_vehicle vehicle;enum de_result input=inputs(e,snapshot,now,&pose,&vehicle,NULL);
     struct lp_tracker probe={0};struct lp_command command;struct lp_metrics measured={0};
     if(input)valid=false;
     else{
      enum lp_result result=lp_track(&probe,path,&snapshot->world,pose,&vehicle,e->config,.02f,&command,&measured,&e->budget);
      if(result!=LP_PATH_FOUND&&result!=LP_STOP){e->search_reason=result;valid=false;}
     }
    }
    if(valid&&dw_source_valid(&e->maps,current))valid=false;
    (void)dw_release(&e->maps,current);
   }
  }
  if(valid){
   if(e->path_revision==UINT64_MAX||e->maneuver_revision==UINT64_MAX)valid=false;
   else{
    path->revision=++e->path_revision;
    if(!e->maneuver_revision)e->maneuver_revision=1;
    if(e->maneuver_active){
     path->merge_goal=e->maneuver_goal;
     path->merge_passed_reference_revision=e->merge_passed_reference_revision;
     if(!path->direction)path->direction=e->maneuver_direction;
    }
    if(path->direction){
     if(e->maneuver_active&&e->maneuver_direction!=path->direction){
      /* The merge milestone and its checked context are unchanged above.
       * New avoidance direction does not undo its observed physical passage. */
      e->maneuver_revision++;
     }
     e->maneuver_active=true;e->maneuver_direction=path->direction;e->maneuver_goal=path->merge_goal;
     strcpy(e->maneuver_frame,path->frame);strcpy(e->maneuver_odom,path->odom);
     strcpy(e->maneuver_configuration,path->configuration);strcpy(e->maneuver_reference,path->reference);
    }
    path->maneuver_revision=e->maneuver_revision;
   }
  }
  e->plan_finalized=true;e->plan_valid=valid;
 }
 bool valid=e->plan_valid&&!e->stopping;
 enum de_result rc=publish(e,e->planning_path,valid);if(rc==DE_BUSY)return rc;
 enum dw_result released=dw_release(&e->maps,e->planning_map);if(released==DW_BUSY)return DE_BUSY;
 e->planning=false;e->planning_snapshot=NULL;
 unsigned outcome=(unsigned)atomic_load(&e->search_reason);
 if(outcome<=LP_CLOCK){
  e->search_outcomes[outcome]++;
  if(outcome==LP_NODE_BUDGET)atomic_fetch_add(&e->search_exhausted,1);
  if(outcome!=LP_PATH_FOUND&&e->first_search_failure==LP_SEARCHING)e->first_search_failure=(enum lp_result)outcome;
 }
 const struct lp_metrics *m=&e->search.metrics;struct lp_metrics *total=&e->planned;
 total->expanded+=m->expanded;total->primitives+=m->primitives;total->footprints+=m->footprints;
 total->cells+=m->cells;total->road_checks+=m->road_checks;total->cancel_checks+=m->cancel_checks;total->clock_checks+=m->clock_checks;
 if(m->nodes_high_water>total->nodes_high_water)total->nodes_high_water=m->nodes_high_water;
 if(m->open_high_water>total->open_high_water)total->open_high_water=m->open_high_water;
 if(m->index_probes_max>total->index_probes_max)total->index_probes_max=m->index_probes_max;
 if(!valid)e->discarded++;
 return e->last_reason=valid?DE_OK:e->search_reason==LP_CANCELLED?DE_CANCELLED:DE_PATH;
}
static enum de_result track(struct drive_engine *e,struct lp_tracker *tracker,uint64_t now,float dt,struct lp_command *command,
 dw_vehicle *vehicle,struct lp_budget budget,struct de_reference *completed,struct de_witness *witness){
 memset(command,0,sizeof(*command));command->emergency=true;command->result=LP_INPUT;
 struct dw_handle mh;const struct dw_snapshot *map;
 if(dw_acquire(&e->maps,&mh,&map))return DE_MAP;
 dw_pose pose;uint64_t map_end=0;enum de_result rc=inputs(e,map,now,&pose,vehicle,&map_end);
 struct de_reference ph;const struct lp_path *path=NULL;
 if(!rc)rc=de_path_acquire(e,&ph,&path);
 if(!rc){
  struct lp_metrics measured={0};
  enum lp_result tracked=lp_track(tracker,path,&map->world,pose,vehicle,e->config,dt,command,&measured,&budget);
  rc=tracked==LP_PATH_FOUND||tracked==LP_STOP?DE_OK:DE_PATH;
  if(tracker==&e->tracker&&(tracked==LP_COLLISION||tracked==LP_INPUT||tracked==LP_NUMERIC))e->path_invalidated=true;
  if(!rc){
   uint64_t pose_end=vehicle->pi_sample_us+(uint64_t)e->config->pose_age_us-vehicle->clock_error_us;
   command->valid_until_pi_us=map_end<pose_end?map_end:pose_end;
   if(command->valid_until_pi_us>map->valid_until_us)command->valid_until_pi_us=map->valid_until_us;
  }
  if(tracker==&e->tracker){e->tracking.cells+=measured.cells;e->tracking.footprints+=measured.footprints;}
  if(!rc&&completed)*completed=ph;
  if(!rc&&witness){witness->reference_revision=map->world.reference_revision;witness->observation_sequence=map->world.observation_seq;
   witness->valid_until_pi_us=command->valid_until_pi_us;strcpy(witness->map_epoch,map->world.map_epoch);}
  if(de_path_release(e,ph))rc=DE_BUSY;
 }
 if(!rc&&dw_source_valid(&e->maps,mh))rc=DE_CHANGED;
 if(dw_release(&e->maps,mh))rc=DE_BUSY;
 return e->last_reason=rc;
}
enum de_result de_track(struct drive_engine *e,uint64_t now,float dt,struct lp_command *command,dw_vehicle *vehicle,struct lp_budget budget){
 enum de_result rc=track(e,&e->tracker,now,dt,command,vehicle,budget,&e->last_track_path,&e->last_track_world);
 if(!rc)atomic_fetch_add(&e->completed,1);
 return rc;
}
enum de_result de_prepare(struct drive_engine *e,uint64_t now,struct de_reference *path,struct de_witness *witness,dw_vehicle *vehicle,struct lp_budget budget){
 struct lp_tracker temporary={0};struct lp_command calculated;
 return track(e,&temporary,now,.02f,&calculated,vehicle,budget,path,witness);
}
enum de_result de_apply(struct drive_engine *e,const dw_state *state,uint64_t now,struct lp_budget budget){
 if(e->planning||e->apply_pending)return DE_BUSY;
 struct dw_handle mh;const struct dw_snapshot *map;
 if(dw_acquire(&e->maps,&mh,&map))return DE_MAP;
 dw_pose pose;dw_vehicle vehicle;enum de_result rc=inputs(e,map,now,&pose,&vehicle,NULL);
 struct de_reference ph;struct lp_path *path=NULL;
 if(!rc&&(strcmp(state->frame_epoch,map->world.frame_epoch)||strcmp(state->odom_epoch,map->world.odom_epoch)))rc=DE_FRAME;
 if(!rc&&(strcmp(state->configuration,map->world.configuration)||strcmp(state->primitive,e->assets->sha256)||strcmp(state->calibration,map->world.calibration)))rc=DE_CONFIGURATION;
 if(!rc&&strcmp(state->reference_id,map->world.reference_id))rc=DE_REFERENCE;
 if(!rc&&state->world_features!=map->world.required_features)rc=DE_CONFIGURATION;
 if(!rc&&(!state->complete||state->path_count<2||state->path_count>LP_PATH))rc=DE_STATE;
 if(!rc)rc=reserve(e,&ph,&path);
 if(!rc){
  memset(path,0,sizeof(*path));path->count=state->path_count;memcpy(path->points,state->path,path->count*sizeof(*path->points));
  path->merge_goal=state->merge_goal;path->goal=path->points[path->count-1].pose;
  path->world_features=state->world_features;
  path->merge_passed_reference_revision=state->has_merge_passed_reference_revision&&
   state->merge_passed_reference_revision==map->world.reference_revision?state->merge_passed_reference_revision:0;
  path->revision=state->path_revision;path->maneuver_revision=state->maneuver_revision;
  path->direction=state->direction;path->phase=state->maneuver_phase;
  strcpy(path->frame,state->frame_epoch);strcpy(path->odom,state->odom_epoch);
  strcpy(path->configuration,state->configuration);strcpy(path->reference,state->reference_id);
  struct lp_metrics metrics={0};struct lp_tracker probe={0};struct lp_command calculated;
  enum lp_result valid=lp_validate_path(path,&map->world,e->config,&metrics,&budget);
  if(valid==LP_PATH_FOUND)valid=lp_track(&probe,path,&map->world,pose,&vehicle,e->config,.02f,&calculated,&metrics,&budget);
  e->last_state_result=valid;
  bool usable=valid==LP_PATH_FOUND||valid==LP_STOP;
  e->apply_path=ph;e->apply_valid=usable;e->apply_pending=true;
  rc=de_apply_finish(e);
 }
 if(dw_release(&e->maps,mh))rc=DE_BUSY;
 return rc;
}
enum de_result de_apply_finish(struct drive_engine *e){
 if(!e->apply_pending)return DE_WAITING;
 enum de_result rc=publish(e,e->apply_path,e->apply_valid);if(rc==DE_BUSY)return rc;
 e->apply_pending=false;
 if(e->apply_valid&&rc==DE_OK){
  const struct lp_path *path=&e->paths[e->apply_path.slot].path;
  e->maneuver_active=path->direction!=0;e->maneuver_direction=path->direction;e->maneuver_goal=path->merge_goal;e->maneuver_revision=path->maneuver_revision;
  e->merge_passed_reference_revision=path->merge_passed_reference_revision;
  if(e->path_revision<path->revision)e->path_revision=path->revision;
  strcpy(e->maneuver_frame,path->frame);strcpy(e->maneuver_odom,path->odom);
  strcpy(e->maneuver_configuration,path->configuration);strcpy(e->maneuver_reference,path->reference);
 }
 return e->apply_valid?rc:DE_STATE;
}
void de_stop(struct drive_engine *e){atomic_store(&e->stopping,true);}
enum de_result de_restart(struct drive_engine *e){
 if(!e->stopping||e->planning||e->apply_pending)return DE_BUSY;
 if(!take(e))return DE_BUSY;
 for(unsigned k=0;k<DE_PATH_SLOTS;k++)if(readers(&e->paths[k])||e->paths[k].writing){give(e);return DE_BUSY;}
 if(e->epoch==UINT32_MAX||e->requested==UINT32_MAX){give(e);return DE_LIMIT;}
 e->epoch++;e->current=-1;e->started=0;atomic_fetch_add(&e->requested,1);
 memset(&e->tracker,0,sizeof(e->tracker));e->completed=0;e->stopping=false;
 e->maneuver_active=false;e->maneuver_direction=0;e->maneuver_revision=0;e->merge_passed_reference_revision=0;
 give(e);return DE_OK;
}
const char *de_reason(enum de_result rc){
 static const char *const reasons[]={"OK","BUSY","INPUT_INVALID","STATE_PREPARING","MAP_UNAVAILABLE","POSE_UNAVAILABLE",
  "PATH_UNAVAILABLE","OBSERVATION_EXPIRED","CONTEXT_CHANGED","STATE_REJECTED","RESOURCE_LIMIT","CANCELLED","CLOCK_ERROR",
  "STATE_FRAME_CHANGED","STATE_CONFIGURATION_CHANGED","STATE_REFERENCE_CHANGED"};
 return (unsigned)rc<sizeof(reasons)/sizeof(*reasons)?reasons[rc]:"INTERNAL_ERROR";
}

enum de_result de_memory_read(struct drive_engine *e,unsigned region,struct dw_memory *out){
 if(!out||region>3)return DE_INPUT;
 if(region==0)return dw_memory_read(&e->maps,out)==DW_OK?DE_OK:DE_BUSY;
 if(region==3)return dw_odom_memory_read(&e->odometry,out)==DW_OK?DE_OK:DE_BUSY;
 if(region==2){
  /* Allocated nodes at the last completed slice, not transient stack/heap use.
   * The index/heap/heuristic workspace is included only in reserved bytes. */
  unsigned used=atomic_load(&e->search_used);
  unsigned peak=atomic_load(&e->search_peak);
  *out=(struct dw_memory){.reserved=sizeof(e->search),.capacity=(uint64_t)e->config->node_limit*sizeof(e->search.nodes[0]),
   .used=(uint64_t)used*sizeof(e->search.nodes[0]),
   .peak=(uint64_t)peak*sizeof(e->search.nodes[0]),
   .exhausted=atomic_load(&e->search_exhausted)};
  return DE_OK;
 }
 if(atomic_flag_test_and_set_explicit(&e->lock,memory_order_acquire))return DE_BUSY;
 unsigned used=0;
 for(unsigned i=0;i<DE_PATH_SLOTS;i++)if((int)i==e->current||e->paths[i].writing||readers(&e->paths[i]))used++;
 *out=(struct dw_memory){.reserved=sizeof(e->paths),.capacity=DE_PATH_SLOTS*sizeof(struct lp_path),
  .used=used*sizeof(struct lp_path),.peak=e->path_peak*sizeof(struct lp_path),
  .exhausted=e->path_exhausted,.contention=atomic_load(&e->resource_contention)};
 give(e);return DE_OK;
}
