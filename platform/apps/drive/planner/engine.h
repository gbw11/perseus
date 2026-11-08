/* SPDX-License-Identifier: Apache-2.0 */
/* Product AUTO ownership boundary. One planner owner, one tracker owner and
 * one state publisher. Fixed pools; no sockets, authority or operating defaults. */
#ifndef XENOVA_DRIVE_ENGINE_H
#define XENOVA_DRIVE_ENGINE_H
#include "lattice.h"
#include "behavior.h"
#include "parking.h"
#define DE_PATH_SLOTS 3
enum de_result { DE_OK,DE_BUSY,DE_INPUT,DE_WAITING,DE_MAP,DE_POSE,DE_PATH,
 DE_EXPIRED,DE_CHANGED,DE_STATE,DE_LIMIT,DE_CANCELLED,DE_CLOCK,DE_FRAME,DE_CONFIGURATION,DE_REFERENCE };
/* Tracker, supervisor, state exporter and one planner preparation probe. */
#define DE_READERS 4
struct de_path_slot {struct lp_path path;atomic_uint generation,leases[DE_READERS];bool writing;uint64_t scene_generation;};
struct de_reference {unsigned slot;uint32_t generation,lease;};
struct de_witness {uint64_t reference_revision,observation_sequence,valid_until_pi_us;char map_epoch[37];};
struct drive_engine {
 atomic_ullong resource_contention;
 uint64_t path_peak,path_exhausted;
 atomic_uint search_used,search_peak;
 atomic_ullong search_exhausted;
 struct dw_pool maps;struct dw_odometry odometry;
 struct de_path_slot paths[DE_PATH_SLOTS];atomic_flag lock;
 const struct lp_config *config;const struct lp_assets *assets;
 struct lp_search search;struct lp_tracker tracker;struct lp_metrics tracking,planned;
 struct tr_intent task;struct tr_basis basis;
 struct lp_tracker progress_snapshot;uint32_t progress_generation;uint64_t planning_vehicle_pi_us;
 struct dw_handle planning_map;const struct dw_snapshot *planning_snapshot;
 struct de_reference planning_path;bool planning,plan_finalized,plan_valid;atomic_bool stopping,path_invalidated;int current;
 uint32_t epoch,next_generation,next_lease;uint64_t path_revision;
 atomic_ullong completed,plans,discarded;atomic_uint requested;
 uint32_t started;atomic_int last_reason;
 bool receive_pending,receive_awaiting,apply_pending,apply_valid;struct dw_handle receive_handle;
 struct dw_snapshot *receive_snapshot;
 struct de_reference apply_path;
 struct de_reference last_track_path;
 struct de_witness last_track_world;
 bool maneuver_active;dw_pose maneuver_goal;int maneuver_direction;uint64_t maneuver_revision;
 uint64_t merge_passed_reference_revision;
 bool has_grant,planning_has_grant; xenova_drive_MissionGrant grant,planning_grant;
 char maneuver_frame[37],maneuver_odom[37],maneuver_configuration[65],maneuver_reference[65];
 atomic_int search_reason,last_state_result,last_world_result;struct lp_budget budget;
 unsigned search_outcomes[LP_CLOCK+1];enum lp_result first_search_failure;
 /* One planner owner mutates behavior; tracker reads immutable path snapshots. */
 atomic_bool behavior_enabled,local_owner,episode_transition,behavior_failed,facts_current;
 bool episode_request_ready,episode_receipt_pending;
 struct db_limits behavior_limits;struct dp_limits parking_limits;struct db_facts facts;
 struct db_episode episode,restore_episode;struct dp_state parking;
 struct lp_config parking_work_config;
 atomic_bool parking_stop_event,parking_boundary_pending;
 atomic_uint snapshot_requested_generation;
 xenova_drive_DriveControlFacts control_facts;
 xenova_drive_EpisodeSnapshot episode_state,episode_candidate;
 xenova_drive_EpisodeStatus episode_status;
 xenova_drive_EpisodeRequest episode_request;
 uint8_t behavior_incarnation[16];uint64_t episode_counter,episode_request_sequence;
 char phase_state_message_id[37];uint64_t last_actual_calculation;
 uint64_t episode_began_guest_us,phase_began_guest_us;
 xenova_drive_MissionGrant apply_grant;bool apply_has_grant;
 uint32_t apply_map_generation;struct lp_budget apply_budget;
};
/* Metadata-only, nonblocking. Search values are published by its sole owner
 * at completed slice boundaries; the search arrays are never scanned here. */
enum de_result de_memory_read(struct drive_engine *,unsigned region,struct dw_memory *);
void de_init(struct drive_engine *,const struct lp_config *,const struct lp_assets *);
/* Inputs have authenticated source/clock metadata. Receive only publishes a
 * complete immutable map; it never performs search or reports app progress. */
enum de_result de_world(struct drive_engine *,const uint8_t *,size_t,uint64_t);
/* Receive owner drains an aborted writer before join. BUSY retains ownership. */
enum de_result de_receive_drain(struct drive_engine *);
/* Map owner only: keep the old map while a complete candidate awaits its
 * exact odom anchor. One pending slot, original age; no second decode. */
enum de_result de_receive_poll(struct drive_engine *,uint64_t now_pi_us);
enum de_result de_vehicle(struct drive_engine *,const dw_vehicle *);
/* planner 단일 owner. Link가 확인한 원 receipt만 저장하며 권위 자체를 만들지 않는다. */
enum de_result de_mission(struct drive_engine *,const xenova_drive_MissionGrant *,uint64_t now_pi_us);
void de_behavior_enable(struct drive_engine *,const uint8_t incarnation[16]);
enum de_result de_control_facts(struct drive_engine *,const xenova_drive_DriveControlFacts *,uint64_t now_pi_us);
enum de_result de_episode_status(struct drive_engine *,const xenova_drive_EpisodeStatus *,uint64_t now_pi_us);
enum de_result de_episode_receipt(struct drive_engine *,const xenova_drive_EpisodeReceipt *);
/* Read a planner-owned registration candidate; the app adds actual identity/clock. */
bool de_episode_request(struct drive_engine *,xenova_drive_EpisodeRequest *);
void de_episode_submitted(struct drive_engine *,const xenova_drive_EpisodeRequest *);
bool de_registration_state(struct drive_engine *,dw_state *);
bool de_budget_request(struct drive_engine *,xenova_drive_MissionBudgetRequest *);
void de_budget_submitted(struct drive_engine *,const xenova_drive_MissionBudgetRequest *,const uint8_t[32]);
enum de_result de_budget_reply(struct drive_engine *,const xenova_drive_MissionBudgetReply *,uint64_t now_pi_us);
bool de_parking_boundary(struct drive_engine *);
void de_parking_boundary_ready(struct drive_engine *);
enum de_result de_plan_start(struct drive_engine *,uint64_t,struct lp_budget);
enum de_result de_plan_slice(struct drive_engine *,unsigned,uint64_t now_pi_us);
enum de_result de_track(struct drive_engine *,uint64_t,float,struct lp_command *,dw_vehicle *,struct lp_budget);
enum de_result de_track_mission(struct drive_engine *,const xenova_drive_MissionGrant *,uint64_t,float,
 struct lp_command *,dw_vehicle *,struct lp_budget);
/* Actual one-off Standby calculation, with a private tracker. It cannot
 * increment the selected runtime loop or mutate the Active tracker. */
enum de_result de_prepare(struct drive_engine *,uint64_t,struct de_reference *,struct de_witness *,dw_vehicle *,struct lp_budget);
/* Caller has already checked source authority. Full path/geometry/rates and an
 * actual tracker calculation are required before publication, not PUBACK. */
enum de_result de_apply(struct drive_engine *,const dw_state *,uint64_t,struct lp_budget);
/* Same owner polls after BUSY. No re-decode; a changed map is revalidated within
 * the original apply budget before publication. */
/* Fresh Pi upper time is required when a pending Scene-derived candidate commits. */
enum de_result de_apply_finish(struct drive_engine *,uint64_t now_pi_us);
enum de_result de_path_acquire(struct drive_engine *,struct de_reference *,const struct lp_path **);
enum de_result de_path_release(struct drive_engine *,struct de_reference);
/* Stop is a request. The planner owner must finish cancellation and release its
 * map/path before join or the storage can be reused. */
void de_stop(struct drive_engine *);
/* Only after the old tracker/planner owners really joined. Input owners and
 * their map/odometry pools remain live for independent MANUAL service. */
enum de_result de_restart(struct drive_engine *);
const char *de_reason(enum de_result);
#endif
