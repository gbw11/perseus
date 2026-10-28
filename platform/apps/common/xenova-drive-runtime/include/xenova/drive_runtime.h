/* SPDX-License-Identifier: Apache-2.0 */
/* App-owned hooks around real calculation. No algorithm, control authority,
 * background app heartbeat, or additional Store/netfront owner lives here. */
#ifndef XENOVA_DRIVE_RUNTIME_H
#define XENOVA_DRIVE_RUNTIME_H
#include <xenova/diagnostics.h>
#include <xenova/health.h>
#include "xm_zclient.h"
#include <xenova/drive_io.h>

int xd_start(const char *app_id, uint32_t period_ms);
bool xd_stopping(void);
/* Nonblocking owner startup: 0 means worker admitted, not MQTT connected. */
int xd_client_prepare(struct xm_zclient *, const char *control, const char *input);
int xd_client_prepare_dual(struct xm_zclient *, const char *control, const char *input, const char *perception);
/* Bind command expiry to the chosen immutable input snapshot for this cycle. */
int xd_command_input(const char *message_type);
/* Identity copied with this iteration's input snapshot; empty before bootstrap. */
const char *xd_vm_id(void);
int xd_publish(struct xm_zclient *, const char *topic, const char *type, const char *payload);
int xd_poll(struct xm_zclient *, int32_t timeout_ms);
void xd_client_stopped(struct xm_zclient *);
/* Main owner calls this for every return, including xd_start failure. Original
 * app error wins; independent cleanup errors remain in local Core history. */
int xd_finish(struct xm_zclient *, int app_result);
int xd_iteration_begin(void);
int xd_step_begin(void);
int xd_step_end(void);
/* Existing release origin/deadline, not a new per-mode execution allowance. */
uint64_t xd_completion_deadline_us(void);
bool xd_step_deadline_missed(void);
/* Latest local assessment only; no message, I/O or app progress generated. */
void xd_health_record(const struct xdh_health *automatic,const struct xdh_health *manual);
/* Call for every actual command serialization result, including failures. */
void xd_command(struct xm_zclient *, const char *topic, const char *json, int encoded,
		uint32_t generation, float v_mps, float steer_rad, int64_t input_seq,
		int64_t control_seq, int64_t input_age_ms, uint32_t input_timeout_ms);
void xd_iteration_end(void);
uint64_t xd_next_release_us(void);
void xd_latest(struct xm_zclient *, const char *topic, const char *type,
		struct xm_span *, int64_t *age_ms, int64_t *seq);
#endif
