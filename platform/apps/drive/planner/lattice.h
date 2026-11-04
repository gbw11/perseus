/* SPDX-License-Identifier: Apache-2.0 */
/* Allocation-free motion-primitive search and geometric tracker. No authority,
 * sockets or default operating vehicle. Caller owns immutable inputs/deadline. */
#ifndef XENOVA_LATTICE_H
#define XENOVA_LATTICE_H
#include "world.h"

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
 unsigned node_limit,expansion_limit,collision_limit,steering_levels,clock_error_us,clock_lease_us,planner_budget_us,state_period_us;
};
struct lp_primitive_point { float x,y,yaw,steer; };
struct lp_assets { const char *id,*sha256; float resolution,wheelbase,max_steer,steer_rate,speed,duration;
 struct lp_primitive_point motion[LP_STEERS][LP_STEERS][LP_PRIMITIVE_SAMPLES]; };
struct lp_collision_index { const dw_world *world;uint64_t sequence,rows[64],shoulder[64];float cell_age_ms,live_age_us; };
struct lp_budget { uint64_t deadline_us; int (*clock)(void *,uint64_t *); bool (*cancelled)(void *);void *context;
 const struct lp_collision_index *collision_index; };
struct lp_metrics { uint64_t expanded,primitives,footprints,cells,road_checks,cancel_checks,clock_checks;
 unsigned nodes_high_water,open_high_water,index_probes_max; };
struct lp_node { dw_pose pose;float steer,g,f,road_cost;int16_t qx,qy;uint8_t heading,steering,closed;bool deviated;
 int parent,heap;uint8_t edge; };
/* goal ends this executable path; merge_goal belongs to the persistent intent. */
struct lp_path { xenova_drive_PathPoint points[LP_PATH];size_t count;dw_pose goal,merge_goal;
 uint64_t revision,maneuver_revision,merge_passed_reference_revision;uint32_t world_features;int direction;unsigned phase;char frame[37],odom[37],configuration[65],reference[65]; };
struct lp_search {
 struct lp_node nodes[LP_NODES];int heap[LP_NODES],index[LP_INDEX];unsigned used,open;
 const dw_world *world;const struct lp_config *config;const struct lp_assets *assets;
 struct lp_budget budget;struct lp_metrics metrics;dw_pose start,goal;float initial_steer,goal_steer;
 struct lp_collision_index collision_index;
 float heuristic[4096];uint64_t heuristic_expanded;unsigned heuristic_open_high_water;
 enum lp_result result;int preferred_direction;bool reference_obstructed,building_heuristic;
};
struct lp_road_state {dw_pose pose;float arc,offset,curvature,length;};
struct lp_tracker { size_t index;float last_velocity,last_steer;uint64_t completed; };
struct lp_command { float velocity,steer,target_x,target_y,geometric_distance,curvature,known_distance;
 bool emergency;enum lp_result result;uint64_t valid_until_pi_us;unsigned maneuver_phase; };

enum lp_result lp_start(struct lp_search *,const dw_world *,dw_pose,float,dw_pose,
 const struct lp_config *,const struct lp_assets *,struct lp_budget,int preferred_direction);
/* Automatic runtime targets only: blocked terminals may retreat along the
 * supplied reference inside the same horizon. Explicit lp_start goals stay exact.
 * Collision checks share this search's index, work limit and original deadline. */
enum lp_result lp_start_local(struct lp_search *,const dw_world *,dw_pose,float,dw_pose,
 const struct lp_config *,const struct lp_assets *,struct lp_budget,int preferred_direction);
/* Build once per immutable map/configuration; 64 rows, no global cache. */
void lp_index_build(struct lp_collision_index *,const dw_world *,const struct lp_config *);
/* Maximum expansions per slice; collision and cancellation checks are also
 * bounded inside each primitive/footprint. SEARCHING is not completed progress. */
enum lp_result lp_advance(struct lp_search *,unsigned,struct lp_path *);
enum lp_result lp_footprint(const dw_world *,dw_pose,float,const struct lp_config *,struct lp_metrics *,const struct lp_budget *);
enum lp_result lp_validate_path(const struct lp_path *,const dw_world *,const struct lp_config *,struct lp_metrics *,const struct lp_budget *);
enum lp_result lp_track(struct lp_tracker *,const struct lp_path *,const dw_world *,dw_pose,const dw_vehicle *,
 const struct lp_config *,float dt,struct lp_command *,struct lp_metrics *,const struct lp_budget *);
enum lp_result lp_reference_goal(const dw_world *,dw_pose,float,float,dw_pose *);
/* Bounded nearest projection onto the supplied road, never an extrapolation. */
enum lp_result lp_reference_project(const dw_world *,dw_pose,struct lp_road_state *);
/* Distance to the nearest point plus the remaining sampled path arc. */
float lp_path_remaining(const struct lp_path *,dw_pose,size_t *nearest);
const char *lp_reason(enum lp_result);
#endif
