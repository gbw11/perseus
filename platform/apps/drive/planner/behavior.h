/* SPDX-License-Identifier: Apache-2.0 */
/* Bounded local maneuver state. Pi facts are inputs, never actuator authority. */
#ifndef XENOVA_DRIVE_BEHAVIOR_H
#define XENOVA_DRIVE_BEHAVIOR_H
#include "task_reference.h"
struct lp_config;
enum db_phase { DB_NONE,DB_ASSESS,DB_CHANGE_OUT,DB_PASS,DB_REJOIN,DB_FINISHED,DB_HOLD };
enum db_policy { DB_RETURN_HOME=1,DB_KEEP_TARGET=2 };
enum db_reason { DB_OK,DB_CONTEXT,DB_METER,DB_TIME,DB_TRAVEL,DB_NO_PROGRESS,DB_GEOMETRY,DB_OBSERVATION };
struct db_limits {
 uint64_t episode_us,phase_us,progress_us,settle_us;
 float travel_m,progress_m,pass_margin_m,heading_rad,steering_rad;
 unsigned policy;
};
/* Normalized original Pi evidence; the transport validates identity/expiry. */
struct db_facts {
 bool valid,meter_valid,stop_applied,stationary,steering_settled;
 uint64_t expires_pi_us,vehicle_sequence,meter_revision,meter_ticks;double metres_per_tick;
 uint64_t sampled_pi_us,clock_error_us;
 uint64_t stop_calculation,stop_serial,control_generation,plan_revision,phase_revision;
 uint8_t meter_epoch[16];
 char pi_session[37],device_session[37],odom_epoch[37];
};
struct db_blocker {
 bool present,unknown;dw_point lower,upper;float station_lower,station_upper;
 uint64_t observation_sequence;char map[37];
};
struct db_episode {
 bool active,terminal,waiting_stop;enum db_phase phase;enum db_reason reason;
 uint8_t id[16];uint64_t revision,phase_revision,started_pi_us,deadline_pi_us;
 uint64_t phase_entered_pi_us,phase_deadline_pi_us,last_progress_pi_us,wait_started_pi_us,waited_us;
 uint64_t meter_start_ticks,meter_used_ticks,meter_revision,last_vehicle_sequence,settle_since_pi_us;double metres_per_tick;
 uint32_t home_lane_id,target_lane_id;unsigned policy;float progress_lower,remaining_upper;
 dw_pose phase_goal;struct db_blocker blocker;struct tr_basis basis;
 uint8_t meter_epoch[16];
 char pi_session[37],device_session[37],odom_epoch[37];
};
bool db_limits_valid(const struct db_limits *);
/* Original absolute meter plus a speed bound over its uncovered age. */
double db_travel_upper(const struct db_facts *,uint64_t,uint64_t,float);
/* Ambiguous correspondence never supplies a station or progress proof. */
bool db_station_bounds(const struct tr_basis *,dw_pose,float,const struct lp_config *,float *,float *);
bool db_station_point(const struct tr_basis *,dw_point,float,float *);
bool db_blocker_observe(const dw_world *,const struct tr_intent *,dw_pose,float,
 const struct lp_config *,struct db_blocker *);
bool db_episode_begin(struct db_episode *,const uint8_t[16],uint32_t,uint32_t,
 const struct tr_basis *,const struct db_blocker *,dw_pose,uint64_t,const struct db_facts *,const struct db_limits *);
enum db_reason db_episode_step(struct db_episode *,const dw_world *,dw_pose,const dw_vehicle *,
 uint64_t,const struct db_facts *,const struct db_limits *,const struct lp_config *,bool normal_stop_wait);
/* A real phase entry may change its goal, never the birth time/meter/episode E. */
bool db_episode_phase(struct db_episode *,enum db_phase,dw_pose,uint64_t,const struct db_limits *);
void db_episode_hold(struct db_episode *,enum db_reason);
#endif
