/* SPDX-License-Identifier: Apache-2.0 */
/* Actual product planner/tracker and independent MANUAL adapter. Search has a
 * separate lower-priority owner. Complete state application is a planner event,
 * not an acknowledgement on reception or a periodic Standby calculation. */
#include "lattice_app.h"
#include "lattice_input.h"
#include <zephyr/random/random.h>
#include <zephyr/sys/atomic.h>
#include <errno.h>
#include <math.h>
#include <string.h>
#include <stdio.h>

extern const struct lp_config lp_selected_config;
extern const struct lp_assets lp_selected_assets;
static struct drive_engine engine;
static struct manual_cfg manual_config;
static struct k_thread planner_thread;
K_THREAD_STACK_DEFINE(planner_stack,8192);
K_MUTEX_DEFINE(app_metadata);
static atomic_t stop,planner_stop;
static bool planner_started,planner_joined,initialized;
static char app_instance[37];
static bool locally_active;
static uint32_t current_generation;
static uint64_t preparation_sequence,snapshot_revision;
static struct de_reference prepared;
static atomic_bool prepared_valid;
static char copy_message[37];
static uint64_t copy_revision,prepared_expiry;
static char prepared_gateway[37];
struct completion {uint64_t sequence,vehicle_sequence,finished_us;size_t tracker_index;uint32_t path_generation,maneuver_phase;char instance[37];struct de_witness world;};
static struct completion completion;
static uint64_t exported_completion;

static int clock_us(void *unused,uint64_t *out){ARG_UNUSED(unused);return xdiag_clock_us(out);}
static bool cancelled(void *unused){ARG_UNUSED(unused);return atomic_get(&stop)||atomic_get(&planner_stop)||xd_stopping();}
static bool tracker_cancelled(void *unused){ARG_UNUSED(unused);return atomic_get(&stop)||drive_task_stopping(DRIVE_TASK_AUTO)||xd_stopping();}
static int current_vehicle(struct dl_clock *clock,uint64_t *now,dw_vehicle *vehicle){
 int rc=dl_clock_get(clock,now);if(rc)return rc;
 struct xt_bootstrap boot;if(xd_context_get(&boot))return -EAGAIN;
 if(dw_vehicle_latest(&engine.odometry,vehicle)||strcmp(vehicle->host_boot,boot.identity.host_boot_id)||
  strcmp(vehicle->odom_epoch,clock->trust.odom_epoch)||*now<vehicle->pi_sample_us||
  *now-vehicle->pi_sample_us+vehicle->clock_error_us>lp_selected_config.pose_age_us)return -ENODATA;
 return 0;
}
static int64_t valid_until(const struct dl_clock *clock,const dw_vehicle *vehicle){
 int64_t pi=(int64_t)vehicle->pi_sample_us+(int64_t)lp_selected_config.pose_age_us-vehicle->clock_error_us;
 if(pi<=clock->upper)return 0;
 int64_t end=(pi-clock->upper)/1000;
 return end<(int64_t)(clock->expires_guest/1000)?end:(int64_t)(clock->expires_guest/1000);
}
static const char *qualified(const void *unused,struct xm_span input,int64_t age){
 ARG_UNUSED(unused);ARG_UNUSED(input);ARG_UNUSED(age);
 if(engine.stopping)return "STATE_PREPARING";
 if(engine.path_invalidated)return "PATH_INVALIDATED";
 struct dl_clock clock;uint64_t now;dw_vehicle vehicle;
 if(current_vehicle(&clock,&now,&vehicle))return "POSE_UNAVAILABLE";
 struct de_reference ph;const struct lp_path *path;
 if(de_path_acquire(&engine,&ph,&path))return "STATE_PREPARING";
 bool applied=false;
 if(!k_mutex_lock(&app_metadata,K_NO_WAIT)){
  uint64_t guest=0;
  applied=!clock_us(NULL,&guest)&&prepared_valid&&prepared.generation==ph.generation&&guest<=prepared_expiry;
  k_mutex_unlock(&app_metadata);
 }
 struct dw_handle mh;const struct dw_snapshot *map;const char *reason="WORLD_UNAVAILABLE";
 if(!dw_acquire(&engine.maps,&mh,&map)){
  dw_pose pose;
  if(dw_snapshot_time(map,now,(uint64_t)lp_selected_config.live_age_us,NULL))reason="OBSERVATION_EXPIRED";
  else if(dw_current_pose(&map->world,&engine.odometry,now,lp_selected_config.pose_age_us,&pose,NULL))reason="POSE_UNAVAILABLE";
  else if(strcmp(path->frame,map->world.frame_epoch)||strcmp(path->odom,map->world.odom_epoch)||strcmp(path->configuration,map->world.configuration)||strcmp(path->reference,map->world.reference_id))reason="CONTEXT_CHANGED";
  else reason=applied?NULL:"STATE_PREPARING";
  (void)dw_release(&engine.maps,mh);
 }
 (void)de_path_release(&engine,ph);return reason;
}
static bool state_usable(const void *context){ARG_UNUSED(context);return !engine.stopping&&atomic_load(&prepared_valid);}
static const char *manual_qualified(const void *unused,struct xm_span input,int64_t age){
 ARG_UNUSED(unused);struct xm_manual_sample sample;uint32_t keys;
 if(!input.p)return "INPUT_UNAVAILABLE";
 if(age<0||age>manual_config.input_timeout_ms)return "INPUT_STALE";
 if(xm_drive_input_parse(input,&keys))return "INPUT_INVALID";
 int rc=xm_manual_sample_parse(input,&sample);
 if(rc!=-ENOMSG&&(rc||!sample.valid||(uint64_t)age+sample.age_ms>manual_config.input_timeout_ms))return "INPUT_INVALID";
 struct dl_clock clock;uint64_t now;dw_vehicle vehicle;
 return current_vehicle(&clock,&now,&vehicle)?"POSE_UNAVAILABLE":NULL;
}
static int automatic(void *unused,struct xm_span control,struct xm_span input,int64_t age,uint64_t deadline,struct drive_sample *out){
 ARG_UNUSED(unused);ARG_UNUSED(control);ARG_UNUSED(input);ARG_UNUSED(age);
 struct dl_clock clock;uint64_t now;dw_vehicle vehicle;
 out->binary_input=true;
 if(current_vehicle(&clock,&now,&vehicle)){out->reason="POSE_UNAVAILABLE";return 0;}
 struct lp_budget budget={deadline,clock_us,tracker_cancelled,NULL,NULL};struct lp_command command;
 enum de_result rc=de_track(&engine,now,.02f,&command,&vehicle,budget);
 if(rc){out->reason=rc==DE_PATH?lp_reason(command.result):de_reason(rc);return 0;}
 out->v_mps=command.velocity;out->steer_rad=command.steer;out->nearest_m=command.known_distance;
 out->valid_until_ms=valid_until(&clock,&vehicle);out->input_sequence=(int64_t)vehicle.sequence;
 int64_t map_end=((int64_t)command.valid_until_pi_us-clock.upper)/1000;
 if(map_end<out->valid_until_ms)out->valid_until_ms=map_end;
 out->input_received_ms=((int64_t)vehicle.pi_sample_us-(int64_t)vehicle.clock_error_us-clock.upper)/1000;
 if(out->valid_until_ms<=0){out->v_mps=out->steer_rad=0;out->reason="CLOCK_ERROR";return 0;}
 /* A worker completion is not exported until its real health/deadline
  * assessment is consumed by the coordinator. Preserve the input witness. */
 out->path_generation=engine.last_track_path.generation;out->tracker_index=engine.tracker.index;
 out->maneuver_phase=command.maneuver_phase;
 strcpy(out->map_epoch,engine.last_track_world.map_epoch);out->reference_revision=engine.last_track_world.reference_revision;
 out->observation_sequence=engine.last_track_world.observation_sequence;
 return 0;
}
static float bounded_float(float value,float lo,float hi){return fminf(hi,fmaxf(lo,value));}
static int manual(void *unused,struct xm_span control,struct xm_span input,int64_t age,uint64_t deadline,struct drive_sample *out){
 ARG_UNUSED(unused);ARG_UNUSED(deadline);
 const char *reason=manual_qualified(NULL,input,age);if(reason){out->reason=reason;return 0;}
 struct dl_clock clock;uint64_t now;dw_vehicle vehicle;
 if(current_vehicle(&clock,&now,&vehicle)){out->reason="POSE_UNAVAILABLE";return 0;}
 float steer=vehicle.has_measured_steering_rad?vehicle.measured_steering_rad:vehicle.has_estimated_steering_rad?vehicle.estimated_steering_rad:NAN;
 if(!isfinite(steer)||!vehicle.has_steering_error_rad||vehicle.steering_error_rad>lp_selected_config.max_steering_error){out->reason="STEERING_UNAVAILABLE";return 0;}
 struct manual_inputs inputs={.control_payload=control,.input_payload=input,.input_age_ms=age};struct manual_command target;const char *state;
 manual_step(&manual_config,&inputs,&target,&state);
 float allowed=lp_selected_config.max_steer-vehicle.steering_error_rad;
 out->steer_rad=steer+bounded_float(bounded_float(target.steer_rad,-allowed,allowed)-steer,-lp_selected_config.steer_rate*.02f,lp_selected_config.steer_rate*.02f);
 float requested=bounded_float(target.v_mps,-lp_selected_config.max_reverse_velocity,lp_selected_config.max_velocity);
 if(vehicle.velocity_mps*requested<0)requested=0;
 out->v_mps=vehicle.velocity_mps+bounded_float(requested-vehicle.velocity_mps,-lp_selected_config.max_brake*.02f,lp_selected_config.max_accel*.02f);
 if(requested==0)out->v_mps=0; /* request stop, never a physical-stop assertion */
 out->valid_until_ms=valid_until(&clock,&vehicle);return 0;
}
void drive_lattice_adapter(struct drive_adapter *adapter,bool is_manual){
 *adapter=(struct drive_adapter){.calculate_until=is_manual?manual:automatic,.input_timeout_ms=200,
  .state_ready=true,.requires_state_sync=!is_manual,.state_usable=is_manual?NULL:state_usable,
  .qualify=is_manual?manual_qualified:qualified,.manual_input=is_manual};
}
static int prepare_current(const dw_state *copy){
 struct dl_clock mapping;uint64_t now;if(dl_clock_get(&mapping,&now))return -EAGAIN;
 uint64_t guest;if(clock_us(NULL,&guest))return -EIO;
 struct lp_budget budget={guest+lp_selected_config.planner_budget_us,clock_us,cancelled,NULL,NULL};
 struct de_reference proof;struct de_witness witness;dw_vehicle vehicle;
 enum de_result rc=de_prepare(&engine,now,&proof,&witness,&vehicle,budget);if(rc)return -EAGAIN;
 struct xt_bootstrap boot;if(xd_context_get(&boot))return -EAGAIN;
 struct de_reference ph;const struct lp_path *path;if(de_path_acquire(&engine,&ph,&path))return -EAGAIN;
 xenova_drive_AppliedState result={0};
 strcpy(result.vm,boot.identity.vm_id);strcpy(result.execution,boot.identity.execution_id);strcpy(result.app_instance,boot.auto_instance_id);
 strcpy(result.host_boot,boot.identity.host_boot_id);strcpy(result.gateway_instance,mapping.gateway);
 result.has_copy_message_id=copy!=NULL;if(copy)strcpy(result.copy_message_id,copy->message_id);
 result.state_revision=copy?copy->state_revision:path->revision;
 strcpy(result.frame_epoch,path->frame);strcpy(result.odom_epoch,path->odom);strcpy(result.configuration,path->configuration);
 strcpy(result.calibration,lp_selected_config.calibration);strcpy(result.primitive,lp_selected_assets.sha256);strcpy(result.reference_id,path->reference);
 result.reference_revision=witness.reference_revision;result.path_revision=path->revision;result.vehicle_sequence=vehicle.sequence;
 result.has_vehicle_sequence=true;
 result.maneuver_revision=path->maneuver_revision;
 result.rebuilt=copy==NULL;result.applied=ph.generation==proof.generation;
 if(!k_mutex_lock(&app_metadata,K_NO_WAIT)){
  if(!engine.stopping&&!strcmp(app_instance,boot.auto_instance_id)&&preparation_sequence<UINT64_MAX){
   int64_t bound=((int64_t)witness.valid_until_pi_us-mapping.upper)/1000;
   uint64_t ended=0;
   if(bound<=0||clock_us(NULL,&ended)||ended>(uint64_t)bound*1000){
    result.applied=false;result.has_reason=true;strcpy(result.reason,"PREPARATION_EVIDENCE_EXPIRED");
   }
   if(!copy&&prepared.generation==proof.generation&&!strcmp(prepared_gateway,mapping.gateway)&&copy_message[0]){
    result.has_copy_message_id=true;strcpy(result.copy_message_id,copy_message);result.state_revision=copy_revision;result.rebuilt=false;
   }
   result.application_sequence=++preparation_sequence;result.has_calculation_sequence=true;result.calculation_sequence=result.application_sequence;
   prepared=proof;prepared_valid=result.applied;
   prepared_expiry=bound>0?(uint64_t)bound*1000:0;strcpy(prepared_gateway,mapping.gateway);
   if(result.has_copy_message_id){strcpy(copy_message,result.copy_message_id);copy_revision=result.state_revision;}
   else{copy_message[0]=0;copy_revision=0;}
  }
  k_mutex_unlock(&app_metadata);
 }
 (void)de_path_release(&engine,ph);
 return result.calculation_sequence?dl_applied(&result):-EAGAIN;
}
static void reject_state(const dw_state *copy,enum de_result reason){
 struct dl_clock clock;uint64_t now;struct xt_bootstrap boot;
 if(dl_clock_get(&clock,&now)||xd_context_get(&boot))return;
 xenova_drive_AppliedState result={0};
 strcpy(result.vm,boot.identity.vm_id);strcpy(result.execution,boot.identity.execution_id);strcpy(result.app_instance,boot.auto_instance_id);
 strcpy(result.host_boot,boot.identity.host_boot_id);strcpy(result.gateway_instance,clock.gateway);
 result.has_copy_message_id=true;strcpy(result.copy_message_id,copy->message_id);result.state_revision=copy->state_revision;
 strcpy(result.frame_epoch,copy->frame_epoch);strcpy(result.odom_epoch,copy->odom_epoch);strcpy(result.configuration,copy->configuration);
 strcpy(result.calibration,copy->calibration);strcpy(result.primitive,copy->primitive);strcpy(result.reference_id,copy->reference_id);
 result.reference_revision=copy->reference_revision;result.path_revision=copy->path_revision;result.maneuver_revision=copy->maneuver_revision;
 result.rebuilt=false;result.applied=false;result.has_reason=true;
 strcpy(result.reason,reason==DE_STATE?lp_reason(atomic_load(&engine.last_state_result)):de_reason(reason));
 if(!k_mutex_lock(&app_metadata,K_NO_WAIT)){
  if(!engine.stopping&&!strcmp(app_instance,boot.auto_instance_id)&&preparation_sequence<UINT64_MAX)result.application_sequence=++preparation_sequence;
  k_mutex_unlock(&app_metadata);
 }
 if(result.application_sequence)(void)dl_applied(&result);
}
static bool rejected(enum de_result rc){return rc==DE_STATE||rc==DE_FRAME||rc==DE_CONFIGURATION||rc==DE_REFERENCE;}
static void planner(void *a,void *b,void *c){
 ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);
 const dw_state *copy=NULL;int slot=-1;bool applying=false;
 while(!cancelled(NULL)){
  struct dl_clock mapping;uint64_t now,guest;
  if(dl_clock_get(&mapping,&now)||clock_us(NULL,&guest)){k_sleep(K_MSEC(2));continue;}
  if(engine.planning){
   enum de_result rc=de_plan_slice(&engine,4,now);
   if(!engine.planning&&rc==DE_OK)(void)prepare_current(NULL);
  }else if(engine.apply_pending){
   enum de_result rc=de_apply_finish(&engine);
    if(rc!=DE_BUSY){if(rc==DE_OK)(void)prepare_current(copy);else if(rejected(rc))reject_state(copy,rc);dl_state_release(slot);slot=-1;copy=NULL;applying=false;}
  }else{
   if(slot<0)slot=dl_state_take(&copy);
   if(slot>=0){
    bool skip=true;
    if(!k_mutex_lock(&app_metadata,K_NO_WAIT)){
     skip=atomic_load(&prepared_valid)&&(locally_active||!strcmp(copy_message,copy->message_id));
     k_mutex_unlock(&app_metadata);
    }
    if(skip){dl_state_release(slot);slot=-1;copy=NULL;k_sleep(K_MSEC(1));continue;}
    struct lp_budget budget={guest+lp_selected_config.planner_budget_us,clock_us,cancelled,NULL,NULL};
    enum de_result rc=de_apply(&engine,copy,now,budget);applying=engine.apply_pending;
    if(!applying){if(rc==DE_OK)(void)prepare_current(copy);else if(rejected(rc))reject_state(copy,rc);dl_state_release(slot);slot=-1;copy=NULL;}
   }else{
    struct lp_budget budget={guest+lp_selected_config.planner_budget_us,clock_us,cancelled,NULL,NULL};
    enum de_result started=de_plan_start(&engine,now,budget);
    if(started==DE_OK&&!engine.planning)(void)prepare_current(NULL);
   }
  }
  /* Preserve the original search deadline. Active bounded slices need not
   * add a whole kernel tick; higher-priority MANUAL/I/O owners can preempt. */
  if(engine.planning)k_yield();else k_sleep(K_MSEC(1));
 }
 de_stop(&engine);
 while(engine.planning)(void)de_plan_slice(&engine,1,0);
 while(engine.apply_pending)(void)de_apply_finish(&engine);
 if(slot>=0)dl_state_release(slot);
}
static int planner_start(void){
 planner_joined=false;atomic_set(&planner_stop,0);
 planner_started=k_thread_create(&planner_thread,planner_stack,K_THREAD_STACK_SIZEOF(planner_stack),planner,NULL,NULL,NULL,14,0,K_NO_WAIT)!=NULL;
 if(planner_started)k_thread_name_set(&planner_thread,"drive-planner");
 return planner_started?0:-ENOMEM;
}
void drive_lattice_context(const struct xd_inputs *inputs){
 dl_context(inputs);
 if(k_mutex_lock(&app_metadata,K_NO_WAIT))return;
 if(inputs->app_instances[0][0]){
  if(!app_instance[0])strcpy(app_instance,inputs->app_instances[0]);
  else if(strcmp(app_instance,inputs->app_instances[0])){prepared_valid=false;de_stop(&engine);atomic_set(&planner_stop,1);}
 }
 struct xm_vehicle_control control;locally_active=false;
 if(!inputs->control.error&&!xm_vehicle_control_parse((struct xm_span){inputs->control.payload,inputs->control.length},&control)){
  locally_active=!strcmp(control.active_vm_id,inputs->vm_id)&&!strcmp(control.active_app,"autonomous")&&!strcmp(control.mode,"AUTO");
  current_generation=control.generation;
 }
 k_mutex_unlock(&app_metadata);
}
void drive_lattice_completed(const struct drive_completion *work){
 if(!work->sample.binary_input||work->sample.reason||work->health.restrict_function||!work->performed||work->rc)return;
 if(k_mutex_lock(&app_metadata,K_NO_WAIT))return;
 if(!engine.stopping&&!strcmp(app_instance,work->source.app_instance)){
  if(prepared.generation!=work->sample.path_generation){copy_message[0]=0;copy_revision=0;}
  completion=(struct completion){.sequence=work->progress,.vehicle_sequence=(uint64_t)work->sample.input_sequence,
   .tracker_index=work->sample.tracker_index,.path_generation=work->sample.path_generation,.finished_us=work->end_us,.maneuver_phase=work->sample.maneuver_phase};
  strcpy(completion.instance,app_instance);strcpy(completion.world.map_epoch,work->sample.map_epoch);
  completion.world.reference_revision=work->sample.reference_revision;completion.world.observation_sequence=work->sample.observation_sequence;
  prepared.generation=work->sample.path_generation;prepared_valid=true;prepared_expiry=(uint64_t)work->sample.valid_until_ms*1000;
 }
 k_mutex_unlock(&app_metadata);
}
int drive_lattice_start(struct drive_controller *controller){
 if(initialized)return -EALREADY;
 initialized=true;de_init(&engine,&lp_selected_config,&lp_selected_assets);
 manual_cfg_default(&manual_config);manual_config.max_speed_mps=lp_selected_config.max_velocity;
 manual_config.max_reverse_mps=lp_selected_config.max_reverse_velocity;manual_config.max_steer_rad=lp_selected_config.max_steer;
 drive_lattice_adapter(&controller->automatic,false);drive_lattice_adapter(&controller->manual,true);
 int rc=dl_start(&engine);if(rc)return rc;return planner_start();
}
int drive_lattice_reset_poll(int64_t deadline){
 if(!planner_started||planner_joined)return -EINVAL;
 int rc=k_thread_join(&planner_thread,K_NO_WAIT);
 if(rc)return k_uptime_get()>=deadline?-ETIMEDOUT:-EAGAIN;
 planner_joined=true;rc=de_restart(&engine);if(rc){planner_joined=false;return -EAGAIN;}
 struct xt_bootstrap boot;if(xd_context_get(&boot)){de_stop(&engine);planner_joined=false;return -EAGAIN;}
 if(k_mutex_lock(&app_metadata,K_NO_WAIT)){de_stop(&engine);planner_joined=false;return -EAGAIN;}
 strcpy(app_instance,boot.auto_instance_id);prepared_valid=false;memset(&completion,0,sizeof(completion));
 preparation_sequence=snapshot_revision=exported_completion=0;copy_message[0]=0;copy_revision=prepared_expiry=0;prepared_gateway[0]=0;k_mutex_unlock(&app_metadata);
 return planner_start();
}
int drive_lattice_join(int64_t deadline){
 atomic_set(&stop,1);atomic_set(&planner_stop,1);de_stop(&engine);int rc=0;
 if(planner_started&&!planner_joined){int64_t left=deadline-k_uptime_get();if(k_thread_join(&planner_thread,left>0?K_MSEC(left):K_NO_WAIT))rc=-ETIMEDOUT;else planner_joined=true;}
 int io=dl_join(deadline);return rc?rc:io;
}
void drive_lattice_report(void){
 dl_report();
 unsigned path_leases=0,path_writers=0,map_readers=0,map_writers=0;
 for(unsigned k=0;k<DE_PATH_SLOTS;k++){
  path_writers+=engine.paths[k].writing;
  for(unsigned r=0;r<DE_READERS;r++)path_leases+=atomic_load(&engine.paths[k].leases[r])!=0;
 }
 for(unsigned k=0;k<DW_MAP_SLOTS;k++){map_readers+=atomic_load(&engine.maps.slots[k].readers);map_writers+=engine.maps.slots[k].writing;}
 printf("XD_LATTICE_RESOURCES {\"engine_bytes\":%u,\"map_pool_bytes\":%u,\"search_bytes\":%u,\"path_pool_bytes\":%u,\"odom_bytes\":%u,\"path_leases\":%u,\"path_writers\":%u,\"map_readers\":%u,\"map_writers\":%u}\n",
  (unsigned)sizeof(engine),(unsigned)sizeof(engine.maps),(unsigned)sizeof(engine.search),(unsigned)sizeof(engine.paths),
  (unsigned)sizeof(engine.odometry),path_leases,path_writers,map_readers,map_writers);
 printf("XD_LATTICE_SEARCH {\"expanded\":%llu,\"primitives\":%llu,\"footprints\":%llu,\"cells\":%llu,\"cancel_checks\":%llu,\"nodes_high_water\":%u,\"open_high_water\":%u,\"index_probe_high_water\":%u}\n",
  (unsigned long long)engine.planned.expanded,(unsigned long long)engine.planned.primitives,
  (unsigned long long)engine.planned.footprints,(unsigned long long)engine.planned.cells,
  (unsigned long long)engine.planned.cancel_checks,engine.planned.nodes_high_water,engine.planned.open_high_water,engine.planned.index_probes_max);
 printf("XD_LATTICE_LAST_INPUT {\"x_mm\":%d,\"y_mm\":%d,\"yaw_mrad\":%d,\"steer_mrad\":%d,\"goal_x_mm\":%d,\"goal_y_mm\":%d,\"first_search_failure\":\"%s\"}\n",
  (int)(engine.search.start.x*1000),(int)(engine.search.start.y*1000),(int)(engine.search.start.yaw*1000),
  (int)(engine.search.initial_steer*1000),(int)(engine.search.goal.x*1000),(int)(engine.search.goal.y*1000),lp_reason(engine.first_search_failure));
 for(unsigned k=0;k<=LP_CLOCK;k++)if(engine.search_outcomes[k])printf("XD_LATTICE_OUTCOME {\"reason\":\"%s\",\"count\":%u}\n",lp_reason(k),engine.search_outcomes[k]);
 printf("XD_LATTICE {\"plans\":%llu,\"completed\":%llu,\"discarded\":%llu,\"reason\":\"%s\",\"search\":\"%s\",\"path\":%d,\"maps_high_water\":%llu}\n",
  (unsigned long long)atomic_load(&engine.plans),(unsigned long long)atomic_load(&engine.completed),
  (unsigned long long)atomic_load(&engine.discarded),de_reason(atomic_load(&engine.last_reason)),
  lp_reason(atomic_load(&engine.search_reason)),engine.current,(unsigned long long)engine.maps.high_water);
}
int drive_lattice_snapshot(dw_state *out){
 if(engine.stopping)return -EAGAIN;
 struct xt_bootstrap boot;if(xd_context_get(&boot))return -EAGAIN;
 struct completion work;uint32_t generation;uint64_t revision;
 if(k_mutex_lock(&app_metadata,K_NO_WAIT))return -EBUSY;
 uint64_t now;if(clock_us(NULL,&now)){k_mutex_unlock(&app_metadata);return -EIO;}
 if(!locally_active||!completion.sequence||completion.sequence==exported_completion||snapshot_revision==UINT64_MAX||strcmp(completion.instance,boot.auto_instance_id)||
  now<completion.finished_us||now-completion.finished_us>lp_selected_config.pose_age_us){
  k_mutex_unlock(&app_metadata);return -EAGAIN;
 }
 work=completion;generation=current_generation;revision=snapshot_revision+1;k_mutex_unlock(&app_metadata);
 struct de_reference h;const struct lp_path *path;if(de_path_acquire(&engine,&h,&path))return -EBUSY;
 int rc=-EAGAIN;
 if(h.generation==work.path_generation){
  memset(out,0,sizeof(*out));out->major=1;strcpy(out->source_vm,boot.identity.vm_id);strcpy(out->source_execution,boot.identity.execution_id);
  strcpy(out->source_app_instance,boot.auto_instance_id);out->source_generation=generation;out->state_revision=revision;
  strcpy(out->frame_epoch,path->frame);strcpy(out->odom_epoch,path->odom);strcpy(out->configuration,path->configuration);
  strcpy(out->calibration,lp_selected_config.calibration);strcpy(out->primitive,lp_selected_assets.sha256);strcpy(out->reference_id,path->reference);
  out->reference_revision=work.world.reference_revision;out->path_revision=path->revision;out->maneuver_revision=path->maneuver_revision;
  out->direction=path->direction;out->maneuver_phase=work.maneuver_phase;out->merge_goal=path->goal;out->vehicle_sequence=work.vehicle_sequence;
  out->calculation_sequence=work.sequence;out->tracker_index=work.tracker_index;out->complete=true;
  out->path_count=path->count;memcpy(out->path,path->points,path->count*sizeof(*path->points));
  uint8_t random[16];sys_rand_get(random,sizeof(random));xm_uuid4_format(out->message_id,random);
  strcpy(out->map_epoch,work.world.map_epoch);out->observation_seq=work.world.observation_sequence;rc=0;
 }
 (void)de_path_release(&engine,h);
 if(!rc&&!k_mutex_lock(&app_metadata,K_NO_WAIT)){snapshot_revision=revision;exported_completion=work.sequence;k_mutex_unlock(&app_metadata);}
 return rc;
}
