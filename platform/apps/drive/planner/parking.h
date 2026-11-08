/* SPDX-License-Identifier: Apache-2.0 */
/* Finite stable-ID scheduling and selected-slot geometry. No movement permits. */
#ifndef XENOVA_DRIVE_PARKING_H
#define XENOVA_DRIVE_PARKING_H
#include "lattice.h"
enum dp_phase { DP_HOLD,DP_SEARCH,DP_EVALUATE,DP_TARGET_LOCKED,DP_APPROACH,DP_ALIGN_STOP,
 DP_ENTERING,DP_FINAL_VERIFY,DP_CANCELLING,DP_EVAL_STOP };
enum dp_result { DP_PENDING,DP_FEASIBLE,DP_INFEASIBLE,DP_UNKNOWN_BUDGET,DP_INPUT_INVALID,DP_UNKNOWN_FAMILY };
struct dp_limits {float goal_distance,goal_heading,seed_inward_m,yaw_seed_rad,min_control_speed;
 float clearance_sufficient,clearance_band,stationary_speed,yaw_rate;uint64_t final_window_us;};
struct dp_round {
 bool initialized,done;uint32_t ids[8];unsigned count,visited;uint64_t source_revision;
 char map[37],frame[37],configuration[65],calibration[65],primitive[65];
 uint8_t mission_id[16];uint64_t mission_revision;
};
struct dp_rank {unsigned clearance_bucket,cusps;float length,steering;uint32_t slot_id;unsigned seed;};
struct dp_state {
 enum dp_phase phase;enum dp_result result;struct dp_round round;struct dp_rank best_rank;
 bool active,locked,has_best,correction_pending,budget_pending,request_ready,terminal;
 uint64_t phase_revision,goal_revision,phase_entered_pi_us,round_deadline_us,candidate_deadline_us,verify_since_pi_us;
 uint64_t last_vehicle_sequence,last_world_sequence;uint32_t selected_slot;uint64_t selected_slot_revision;
 unsigned evaluated_in_round,stage,seed,published_phase;atomic_bool boundary_ready;bool resuming_plan;
 struct lp_metrics spent;dw_pose goal,approach_goal;struct lp_space space;struct lp_goal_set goals;
 struct lp_path temporary_approach,best_approach,best_maneuver;
 xenova_drive_MissionGrant original_grant; xenova_drive_MissionBudgetRequest request;
 uint8_t selected_goal_sha256[32],plan_sha256[32],request_sha256[32];
};
bool dp_limits_valid(const struct dp_limits *);
bool dp_round_begin(struct dp_round *,const dw_world *,const xenova_drive_MissionGrant *,dw_pose);
uint32_t dp_round_next(const struct dp_round *);
bool dp_round_visit(struct dp_round *,uint32_t);
const xenova_drive_ParkingSlot *dp_slot(const dw_world *,uint32_t);
/* Membership and information are independent: empty/unknown is never eligible. */
bool dp_slot_eligible(const dw_world *,const xenova_drive_ParkingSlot *,unsigned strategy);
bool dp_goal_seeds(const xenova_drive_ParkingSlot *,unsigned,const struct lp_config *,const struct dp_limits *,
 float pose_pad,struct lp_goal_set *,struct lp_space *);
bool dp_goal_contains(const struct lp_space *,dw_pose,float,const struct lp_config *);
/* The pose is a search preference. Full uncertainty containment plus the
 * intended heading hemisphere is the terminal region, not center residual. */
bool dp_terminal_accept(const struct lp_space *,dw_pose,float,dw_pose,float,const struct lp_config *);
bool dp_approach(const dw_world *,const struct tr_intent *,const xenova_drive_ParkingSlot *,const struct lp_config *,dw_pose *);
bool dp_rank_better(const struct dp_rank *,const struct dp_rank *);
struct dp_rank dp_path_rank(const struct lp_path *,float,uint32_t,unsigned,const struct dp_limits *);
#endif
