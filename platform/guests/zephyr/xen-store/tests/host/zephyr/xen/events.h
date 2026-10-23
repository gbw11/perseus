/* SPDX-License-Identifier: Apache-2.0 */
#include <stdint.h>
#define EVTCHN_2L_NR_CHANNELS 4096
int mask_event_channel(uint32_t);
int unmask_event_channel(uint32_t);
int bind_event_channel(uint32_t, void (*)(void *), void *);
int unbind_event_channel(uint32_t);
int notify_evtchn(uint32_t);
