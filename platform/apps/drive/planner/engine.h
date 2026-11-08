/* SPDX-License-Identifier: Apache-2.0 */
/* Product AUTO ownership boundary. One planner owner, one tracker owner and
 * one state publisher. Fixed pools; no sockets, authority or operating defaults. */
#ifndef XENOVA_DRIVE_ENGINE_H
#define XENOVA_DRIVE_ENGINE_H
#include "lattice.h"
#define DE_PATH_SLOTS 3
enum de_result { DE_OK,DE_BUSY,DE_INPUT,DE_WAITING,DE_MAP,DE_POSE,DE_PATH,
 DE_EXPIRED,DE_CHANGED,DE_STATE,DE_LIMIT,DE_CANCELLED,DE_CLOCK,DE_FRAME,DE_CONFIGURATION,DE_REFERENCE };
/* Tracker, supervisor, state exporter and one planner preparation probe. */
#define DE_READERS 4
struct de_path_slot {struct lp_path path;atomic_uint generation,leases[DE_READERS];bool writing;};
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
 bool has_grant; xenova_drive_MissionGrant grant,planning_grant;
 char maneuver_frame[37],maneuver_odom[37],maneuver_configuration[65],maneuver_reference[65];
 atomic_int search_reason,last_state_result,last_world_result;struct lp_budget budget;
 unsigned search_outcomes[LP_CLOCK+1];enum lp_result first_search_failure;
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
/* Same planner owner polls after BUSY; does not decode/validate again. */
enum de_result de_apply_finish(struct drive_engine *);
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
