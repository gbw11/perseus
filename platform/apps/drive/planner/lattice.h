/* SPDX-License-Identifier: Apache-2.0 */
/* Allocation-free motion-primitive search and geometric tracker. No authority,
 * sockets or default operating vehicle. Caller owns immutable inputs/deadline. */
#ifndef XENOVA_LATTICE_H
#define XENOVA_LATTICE_H
#include "world.h"
#include "task_reference.h"

#define LP_NODES 8192
#define LP_INDEX 16384
#define LP_PATH 512
#define LP_STEERS 9
#define LP_PRIMITIVE_SAMPLES 17
enum lp_result { LP_SEARCHING,LP_PATH_FOUND,LP_STOP,LP_NOT_FOUND,LP_NODE_BUDGET,
 LP_WORK_BUDGET,LP_DEADLINE,LP_CANCELLED,LP_INPUT,LP_NUMERIC,LP_COLLISION,LP_PATH_LIMIT,LP_CLOCK };
struct lp_config {
 const char *id,*vehicle,*calibration;
 float resolution,wheelbase,front,rear,half_width,margin,max_steer,steer_rate,max_velocity,
 max_accel,max_brake,max_lateral_accel,lookahead_min,lookahead_max,lookahead_time,
 reaction_s,goal_distance,goal_heading,tracking_error,max_position_error,max_heading_error,max_steering_error,
 primitive_speed,primitive_duration,pose_age_us,live_age_us,cell_age_ms,
 deviation_cost,curvature_cost,switch_cost,plan_horizon,heuristic_weight,heading_weight,tracking_guard,goal_straight_m;
 float max_reverse_velocity;
 unsigned node_limit,expansion_limit,collision_limit,steering_levels,clock_error_us,clock_lease_us,planner_budget_us,state_period_us,direction_phase_us,guest_timestamp_error_us,guest_drift_ppm;
};
struct lp_primitive_point { float x,y,yaw,steer; };
struct lp_assets { const char *id,*sha256; float resolution,wheelbase,max_steer,steer_rate,speed,duration;
 struct lp_primitive_point motion[LP_STEERS][LP_STEERS][LP_PRIMITIVE_SAMPLES]; };
struct lp_collision_index { const dw_world *world;uint64_t sequence,rows[64],shoulder[64];float cell_age_ms,live_age_us; };
struct lp_budget { uint64_t deadline_us; int (*clock)(void *,uint64_t *); bool (*cancelled)(void *);void *context;
 const struct lp_collision_index *collision_index; };
struct lp_metrics { uint64_t expanded,primitives,footprints,cells,road_checks,cancel_checks,clock_checks;
 unsigned nodes_high_water,open_high_water,index_probes_max; };
struct lp_node { dw_pose pose;float steer,g,f,road_cost;int16_t qx,qy;uint8_t heading,steering,closed,cusps;int8_t gear;bool deviated;
 int parent,heap;uint8_t edge; };
/* 완료된 계산/인계 사본의 metadata다. 수신 grant/실행 권위와 동일하지 않다. */
struct lp_execution {bool present;uint32_t features;xenova_drive_MissionExecution mission;
 pb_size_t segments_count;xenova_drive_GearSegment segments[3];
 bool has_direction_gate;xenova_drive_DirectionGateRef direction_gate;};
/* goal ends this executable path; merge_goal belongs to the persistent intent. */
struct lp_path { xenova_drive_PathPoint points[LP_PATH];size_t count;dw_pose goal,merge_goal;
 uint64_t revision,maneuver_revision,merge_passed_reference_revision;uint32_t world_features;int direction;unsigned phase;char frame[37],odom[37],configuration[65],reference[65];
 struct lp_execution execution;struct tr_intent task;struct tr_basis basis;
 size_t initial_tracker_index;uint64_t initial_pi_us;};
struct lp_search {
 struct lp_node nodes[LP_NODES];int heap[LP_NODES],index[LP_INDEX];unsigned used,open;
 const dw_world *world;const struct lp_config *config;const struct lp_assets *assets;
 struct lp_budget budget;struct lp_metrics metrics;dw_pose start,goal;float initial_steer,goal_steer;
 struct lp_collision_index collision_index;
 float heuristic[4096];uint64_t heuristic_expanded;unsigned heuristic_open_high_water;
 enum lp_result result;int preferred_direction;bool reference_obstructed,building_heuristic;
 struct tr_intent task;struct tr_basis basis;
 bool signed_search;int initial_gear,goal_gear;unsigned max_cusps;
};
struct lp_road_state {dw_pose pose;float arc,offset,curvature,length;};
struct lp_tracker { size_t index;float last_velocity,last_steer;uint64_t completed;
 uint64_t path_token,last_pi_us,gate_revision;unsigned segment;float progress;bool initialized; };
/* 실제 현재 owner의 STOP/APPLIED·정지/조향 확인을 소비하는 내부 사실이다.
 * StateCopy.direction_gate 자체는 이 사실을 생산하지 않는다. */
struct lp_direction_fact {uint64_t path_revision,maneuver_revision,calculation_sequence,serial_stop_sequence,gate_revision,expires_pi_us;
 unsigned next_segment;int direction;bool current,stationary,steering_settled;};
struct lp_command { float velocity,steer,target_x,target_y,geometric_distance,curvature,known_distance;
 bool emergency;enum lp_result result;uint64_t valid_until_pi_us;unsigned maneuver_phase;
 unsigned segment_index,cusp_index;int requested_direction;bool gate_required; };

enum lp_result lp_start(struct lp_search *,const dw_world *,dw_pose,float,dw_pose,
 const struct lp_config *,const struct lp_assets *,struct lp_budget,int preferred_direction);
/* Automatic runtime targets only: blocked terminals may retreat along the
 * supplied reference inside the same horizon. Explicit lp_start goals stay exact.
 * Collision checks share this search's index, work limit and original deadline. */
enum lp_result lp_start_local(struct lp_search *,const dw_world *,dw_pose,float,dw_pose,
 const struct lp_config *,const struct lp_assets *,struct lp_budget,int preferred_direction);
enum lp_result lp_start_task(struct lp_search *,const dw_world *,dw_pose,float,dw_pose,
 const struct lp_config *,const struct lp_assets *,struct lp_budget,const struct tr_intent *,const struct tr_basis *,const struct lp_metrics *);
/* 정확한 point goal의 양기어 검색. 도로 local-terminal로 목표를 바꾸지 않는다.
 * initial/final gear는 ±1, 내부 cusp는 0..2이며 모든 family가 같은 budget을 쓴다. */
enum lp_result lp_start_signed(struct lp_search *,const dw_world *,dw_pose,float,dw_pose,
 int initial_gear,int final_gear,unsigned max_cusps,const struct lp_config *,const struct lp_assets *,struct lp_budget);
enum lp_result lp_task_obstructed(const dw_world *,const struct tr_intent *,dw_pose,dw_pose,
 const struct lp_config *,struct lp_budget,struct lp_metrics *,bool *);
/* Build once per immutable map/configuration; 64 rows, no global cache. */
void lp_index_build(struct lp_collision_index *,const dw_world *,const struct lp_config *);
/* Maximum expansions per slice; collision and cancellation checks are also
 * bounded inside each primitive/footprint. SEARCHING is not completed progress. */
enum lp_result lp_advance(struct lp_search *,unsigned,struct lp_path *);
enum lp_result lp_footprint(const dw_world *,dw_pose,float,const struct lp_config *,struct lp_metrics *,const struct lp_budget *);
enum lp_result lp_validate_path(const struct lp_path *,const dw_world *,const struct lp_config *,struct lp_metrics *,const struct lp_budget *);
enum lp_result lp_track(struct lp_tracker *,const struct lp_path *,const dw_world *,dw_pose,const dw_vehicle *,
 const struct lp_config *,float dt,struct lp_command *,struct lp_metrics *,const struct lp_budget *);
/* tracker 단일 owner. 새 immutable path token만 progress를 초기화한다. */
void lp_tracker_begin(struct lp_tracker *,uint64_t path_token);
bool lp_tracker_next_segment(struct lp_tracker *,const struct lp_path *,uint64_t now_pi_us,
 uint64_t original_calculation_sequence,const struct lp_direction_fact *);
enum lp_result lp_reference_goal(const dw_world *,dw_pose,float,float,dw_pose *);
/* Bounded nearest projection onto the supplied road, never an extrapolation. */
enum lp_result lp_reference_project(const dw_world *,dw_pose,struct lp_road_state *);
enum lp_result lp_task_project(const dw_world *,const struct tr_intent *,dw_pose,struct lp_road_state *);
enum lp_result lp_task_goal(const dw_world *,const struct tr_intent *,dw_pose,float,float,dw_pose *);
/* Distance to the nearest point plus the remaining sampled path arc. */
float lp_path_remaining(const struct lp_path *,dw_pose,size_t *nearest);
/* dw_state_validate를 통과한 소유 State만 import한다. 원 actor/권위 검사는 호출자 책임이다. */
void lp_state_import(struct lp_path *,const dw_state *);
bool lp_state_export(const struct lp_path *,dw_state *);
bool lp_mission_matches(const struct lp_path *,const xenova_drive_MissionGrant *);
const char *lp_reason(enum lp_result);
#endif
