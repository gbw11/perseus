/* SPDX-License-Identifier: Apache-2.0 */
/* Bounded binary inputs and immutable references. No socket, planner or authority. */
#ifndef XENOVA_DRIVE_WORLD_H
#define XENOVA_DRIVE_WORLD_H
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdatomic.h>
#include "generated/world.pb.h"

#define DW_WIRE_MAX 32768u
#define DW_MAP_SLOTS 3
#define DW_ODOM_SLOTS 128
#define DW_ROAD_PRIORITY 1u
#define DW_CELL_ROAD 8u
typedef xenova_drive_Pose dw_pose;
typedef xenova_drive_Point dw_point;
typedef xenova_drive_WorldSnapshot dw_world;
typedef xenova_drive_VehicleSample dw_vehicle;
typedef xenova_drive_DriveState dw_state;

enum dw_result { DW_OK, DW_BUSY, DW_TOO_LARGE, DW_DECODE, DW_VERSION, DW_SOURCE,
 DW_ARRAY, DW_GEOMETRY, DW_TIME, DW_CONFIGURATION, DW_STALE, DW_NO_REFERENCE,
 DW_EPOCH, DW_HANDLE, DW_NO_SPACE, DW_NUMERIC };
struct dw_clock { char epoch[37]; int64_t lower_offset_us,upper_offset_us;
 uint64_t expires_pi_us; uint32_t max_error_us; };
struct dw_trust { char vehicle[65],mapper_boot[37],mapper_instance[37],map_epoch[37],frame_epoch[37],odom_epoch[37],configuration[65],
 calibration[65],primitive[65]; struct dw_clock clock; uint64_t max_live_age_us; };
struct dw_snapshot { dw_world world; uint64_t received_us,observed_upper_age_us,valid_until_us; };
struct dw_handle { uint32_t generation; uint8_t slot; uint32_t lease; };
struct dw_slot { struct dw_snapshot snapshot; atomic_uint generation,leases[8],readers; uint32_t source_revision; bool writing; };
struct dw_pool { atomic_flag lock; struct dw_slot slots[DW_MAP_SLOTS]; int current;
 struct dw_trust trust; bool authorized; uint32_t source_revision,next_lease;
 uint32_t next_generation; uint64_t published,rejected,busy,high_water; };
struct dw_odometry { atomic_flag lock; dw_vehicle samples[DW_ODOM_SLOTS]; size_t next,count; };

enum dw_result dw_decode(const uint8_t *,size_t,const struct dw_trust *,uint64_t,struct dw_snapshot *);
enum dw_result dw_vehicle_decode(const uint8_t *,size_t,dw_vehicle *);
enum dw_result dw_state_decode(const uint8_t *,size_t,dw_state *);
enum dw_result dw_link_decode(const uint8_t *,size_t,xenova_drive_DriveLink *);
enum dw_result dw_world_validate(const dw_world *,const struct dw_trust *,uint64_t,uint64_t *);
/* Current Pi upper time versus the original observation lower time. Tightening
 * a guest/Pi clock interval never renews age or requires an estimated receive
 * time to stay monotonic. deadline, when requested, retains the original limit. */
enum dw_result dw_snapshot_time(const struct dw_snapshot *,uint64_t,uint64_t,uint64_t *deadline);
void dw_pool_init(struct dw_pool *);
/* Authenticated connection/configuration owner only. Does not discard the last
 * map or renew its observations; outstanding older writers cannot publish. */
enum dw_result dw_pool_source(struct dw_pool *,const struct dw_trust *);
enum dw_result dw_reserve(struct dw_pool *,struct dw_handle *,struct dw_snapshot **);
enum dw_result dw_publish(struct dw_pool *,struct dw_handle);
enum dw_result dw_abort(struct dw_pool *,struct dw_handle);
enum dw_result dw_acquire(struct dw_pool *,struct dw_handle *,const struct dw_snapshot **);
enum dw_result dw_source_valid(struct dw_pool *,struct dw_handle);
enum dw_result dw_release(struct dw_pool *,struct dw_handle);
enum dw_result dw_odom_add(struct dw_odometry *,const dw_vehicle *);
void dw_odom_init(struct dw_odometry *);
/* Authenticated input owner changes epoch; old map anchors become unusable. */
enum dw_result dw_odom_reset(struct dw_odometry *);
enum dw_result dw_vehicle_latest(struct dw_odometry *,dw_vehicle *);
enum dw_result dw_current_pose(const dw_world *,struct dw_odometry *,uint64_t,uint64_t,dw_pose *,dw_vehicle *);
bool dw_pose_finite(dw_pose);
float dw_angle(float);
dw_pose dw_compose(dw_pose,dw_pose);
dw_pose dw_inverse(dw_pose);
const char *dw_reason(enum dw_result);
#endif
