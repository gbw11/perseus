/* SPDX-License-Identifier: Apache-2.0 */
/* App-owned hooks around real calculation. No algorithm, control authority,
 * background app heartbeat, or additional Store/netfront owner lives here. */
#ifndef XENOVA_DRIVE_RUNTIME_H
#define XENOVA_DRIVE_RUNTIME_H
#include <xenova/diagnostics.h>
#include "xm_zclient.h"

int xd_start(const char *app_id, uint32_t period_ms);
bool xd_stopping(void);
/* Main thread only: defer authenticated v1 setup until its own bootstrap exists. */
int xd_client_prepare(struct xm_zclient *, const char *control, const char *input);
int xd_publish(struct xm_zclient *, const char *topic, const char *type, const char *payload);
int xd_poll(struct xm_zclient *, int32_t timeout_ms);
void xd_client_stopped(struct xm_zclient *);
/* Main owner calls this for every return, including xd_start failure. Original
 * app error wins; independent cleanup errors remain in local Core history. */
int xd_finish(struct xm_zclient *, int app_result);
int xd_iteration_begin(void);
int xd_step_begin(void);
int xd_step_end(void);
/* Call for every actual command serialization result, including failures. */
void xd_command(struct xm_zclient *, const char *topic, const char *json, int encoded,
		uint32_t generation, float v_mps, float steer_rad, int64_t input_seq,
		int64_t control_seq, int64_t input_age_ms);
void xd_iteration_end(void);
uint64_t xd_next_release_us(void);
void xd_latest(struct xm_zclient *, const char *topic, const char *type,
		struct xm_span *, int64_t *age_ms, int64_t *seq);
#endif
